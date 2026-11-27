"""The customer's answer per job (KAN-811, AS1-08; build slice 1, table 3.2;
rulings 30, 48, 49, 167, 172; decisions 31, 54, 64; checks 3 to 8).

| What happens | In state | Result | Refused when |
| --- | --- | --- | --- |
| Record «accepted», with how it came | Draft, waits | Accepted, with who, when and the amount; a job may be accepted before it has a price (ruling 167) | Already accepted; declined; stopped |
| Record «declined» | Draft, waits | Declined | The note is missing; stopped |
| Record «declined» on an accepted job | Accepted | Without clocked time: declined. With clocked time: the clocks stop, the people are told, and the advisor's choice decides — «hours billed» keeps it accepted and stopped, «not billed» declines and stops it (ruling 48, decision 54) | The choice is missing where time was clocked |
| The amount of a job accepted with a price changes | Accepted | Waits for the answer; keeps «accepted before: CHF x» (ruling 30). A text alone changes nothing (decision 31) | – |
| An accepted job is changed while somebody is clocked on it | Accepted | Asks first, naming the person (ruling 49) | Not confirmed |
| A stopped job is changed, answered or clocked | Stopped | – | Always (decision 54) |

`record_answers` is the one write path of an answer (it replaced KAN-810's
`accept_job`). Every answer is its own row, `service_order_job_answer`,
append-only; the job's `answer_state` is the state now. «Wartet auf
Antwort», «Arbeit ohne Antwort» and «accepted before» are worked out on read
(section 3.9), never stored.

**Accepted before a price** (rulings 167, 172): the answer's amount is null,
and positions entered later are the job's content — however many follow,
the job never waits again because of them. The order says who entered each
price and when (`priced_by`/`priced_at` on the position); no screen says the
customer was «shown» a price: a sentence about the past comes from a
recorded event.

**The clock** is KAN-814's. `work_guard` answers «nobody is clocked» until
then, so checks 6 and 8 are built against its two questions here and proved
end to end with KAN-814.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aftersales.models.service_order import (
    AnswerChannel,
    AnswerResult,
    JobAnswerState,
    ServiceOrder,
    ServiceOrderJob,
    ServiceOrderJobAnswer,
    ServiceOrderPosition,
)
from app.aftersales.services import order_money, work_guard
from app.aftersales.services.service_order import order_follows_accepted_job, record_order_change, refuse_frozen
from app.core.base import utcnow
from app.core.errors import ConflictError, NotFoundError, UnprocessableEntityError

_NOTE_LENGTH = 1000
_ANSWERABLE = frozenset({JobAnswerState.DRAFT, JobAnswerState.WAITING})


@dataclass(frozen=True)
class JobAnswerInput:
    job_id: uuid.UUID
    result: AnswerResult
    note: str | None = None
    # The advisor's choice when an accepted job with clocked time is declined (ruling 48).
    hours_billed: bool | None = None


# --- reading -------------------------------------------------------------------------------------


def answers_of(db: Session, *, order_id: uuid.UUID) -> list[ServiceOrderJobAnswer]:
    """Every answer of the order, oldest first."""

    return list(
        db.scalars(
            select(ServiceOrderJobAnswer)
            .where(ServiceOrderJobAnswer.service_order_id == order_id)
            .order_by(ServiceOrderJobAnswer.recorded_at, ServiceOrderJobAnswer.id)
        )
    )


def last_acceptance(answers: list[ServiceOrderJobAnswer], job_id: uuid.UUID) -> ServiceOrderJobAnswer | None:
    accepted = [a for a in answers if a.job_id == job_id and a.result is AnswerResult.ACCEPTED]
    return accepted[-1] if accepted else None


def accepted_before(job: ServiceOrderJob, answers: list[ServiceOrderJobAnswer]) -> ServiceOrderJobAnswer | None:
    """«Accepted before: CHF x» (ruling 30): a job that waits again after the amount it
    was accepted at changed. Worked out, never stored."""

    if job.answer_state is not JobAnswerState.WAITING:
        return None
    last = last_acceptance(answers, job.id)
    return last if last is not None and last.amount_gross is not None else None


def _job_positions(db: Session, job: ServiceOrderJob) -> list[ServiceOrderPosition]:
    return list(db.scalars(select(ServiceOrderPosition).where(ServiceOrderPosition.job_id == job.id)))


# --- the rules -------------------------------------------------------------------------------------


def refuse_stopped(job: ServiceOrderJob) -> None:
    """Decision 54: a stopped job is not changed, answered or clocked again — further work
    is a new position on another job."""

    if job.stopped_at is not None:
        raise ConflictError(
            f"Job «{job.name}» was stopped and can no longer be changed — further work is a new job.",
            details={"reason": "service_order_job_stopped"},
        )


def refuse_unconfirmed_change_while_clocked(
    db: Session, *, order: ServiceOrder, job: ServiceOrderJob, confirmed: bool
) -> None:
    """Ruling 49, check 6: changing an accepted job while somebody is clocked on it asks
    first and names the person. The work goes on; if the change moves the amount, the
    job waits for the answer and the order shows «Arbeit ohne Antwort»."""

    if confirmed or job.answer_state is not JobAnswerState.ACCEPTED:
        return
    people = work_guard.people_clocked_on_job(db, tenant_id=order.tenant_id, job_id=job.id)
    if people:
        names = ", ".join(person.label for person in people)
        raise ConflictError(
            f"{names} is working on job «{job.name}» right now — confirm the change first.",
            details={
                "reason": "service_order_job_clocked_confirm",
                "people": [{"userId": str(p.user_id), "label": p.label} for p in people],
            },
        )


def work_without_answer(db: Session, *, order: ServiceOrder, jobs: list[ServiceOrderJob]) -> set[uuid.UUID]:
    """«Arbeit ohne Antwort» (section 3.9): a job that waits for the answer while
    somebody is clocked on it. Worked out from the answers and the running clocks."""

    return {
        job.id
        for job in jobs
        if job.answer_state is JobAnswerState.WAITING
        and work_guard.people_clocked_on_job(db, tenant_id=order.tenant_id, job_id=job.id)
    }


def follow_amount(db: Session, *, order: ServiceOrder, job: ServiceOrderJob, actor_id: uuid.UUID) -> None:
    """Ruling 30, decision 31, check 5: after a change of the job's positions, a job that
    had a price when it was accepted and whose amount is no longer that amount waits for
    the answer again. A job accepted without a price never does (ruling 172). Call after
    the change is flushed; does not commit."""

    if job.answer_state is not JobAnswerState.ACCEPTED or job.stopped_at is not None:
        return
    db.flush()
    last = last_acceptance(answers_of(db, order_id=order.id), job.id)
    if last is None or last.amount_net is None:
        return
    amount = order_money.job_amount(job, _job_positions(db, job))
    now = (amount.net, amount.gross) if amount else (None, None)
    if now == (last.amount_net, last.amount_gross):
        return
    job.answer_state = JobAnswerState.WAITING
    job.updated_by = actor_id
    record_order_change(
        db, order, action="service_order_job_waits_again", actor_id=actor_id,
        before={"jobId": str(job.id), "answerState": JobAnswerState.ACCEPTED.value,
                "acceptedNet": str(last.amount_net), "acceptedGross": str(last.amount_gross)},
        after={"jobId": str(job.id), "answerState": JobAnswerState.WAITING.value,
               "net": str(now[0]) if now[0] is not None else None,
               "gross": str(now[1]) if now[1] is not None else None},
    )


def _snapshot(job: ServiceOrderJob) -> dict[str, Any]:
    return {
        "jobId": str(job.id),
        "name": job.name,
        "answerState": job.answer_state.value,
        "stopped": job.stopped_at is not None,
        "hoursBilled": job.hours_billed,
    }


def _note(value: str | None) -> str | None:
    note = (value or "").strip()
    if len(note) > _NOTE_LENGTH:
        raise UnprocessableEntityError(
            f"A note has at most {_NOTE_LENGTH} characters.", details={"reason": "service_order_answer_note_too_long"}
        )
    return note or None


def _answer(
    db: Session, *, order: ServiceOrder, job: ServiceOrderJob, data: JobAnswerInput, channel: AnswerChannel,
    actor_id: uuid.UUID, now: Any,
) -> None:
    refuse_stopped(job)
    note = _note(data.note)
    before = _snapshot(job)
    amount = None
    hours_billed = None
    told: list[work_guard.ClockedPerson] = []
    if data.result is AnswerResult.ACCEPTED:
        if job.answer_state not in _ANSWERABLE:
            raise ConflictError(
                f"Job «{job.name}» already has the answer «{job.answer_state.value}».",
                details={"reason": "service_order_job_already_answered", "answerState": job.answer_state.value},
            )
        amount = order_money.job_amount(job, _job_positions(db, job))
        job.answer_state = JobAnswerState.ACCEPTED
        order_follows_accepted_job(order)
    else:
        if note is None:
            raise UnprocessableEntityError(
                f"A declined job needs a note — job «{job.name}».",
                details={"reason": "service_order_answer_note_required", "jobId": str(job.id)},
            )
        if job.answer_state is JobAnswerState.DECLINED:
            raise ConflictError(
                f"Job «{job.name}» already has the answer «declined».",
                details={"reason": "service_order_job_already_answered", "answerState": job.answer_state.value},
            )
        clocked = job.answer_state is JobAnswerState.ACCEPTED and work_guard.time_clocked_on_job(
            db, tenant_id=order.tenant_id, job_id=job.id
        )
        if clocked:
            # Ruling 48: work started — the clocks stop, the people are told, and the
            # advisor says whether the hours are billed.
            if data.hours_billed is None:
                raise UnprocessableEntityError(
                    f"Time was clocked on job «{job.name}» — say whether the hours are billed.",
                    details={"reason": "service_order_answer_hours_billed_required", "jobId": str(job.id)},
                )
            hours_billed = data.hours_billed
            told = work_guard.stop_clocks_on_job(db, tenant_id=order.tenant_id, job_id=job.id, actor_id=actor_id)
            job.stopped_at, job.stopped_by, job.hours_billed = now, actor_id, hours_billed
            job.answer_state = JobAnswerState.ACCEPTED if hours_billed else JobAnswerState.DECLINED
        else:
            job.answer_state = JobAnswerState.DECLINED
        # Its bookings that have not started are released, and the advisor told, with
        # the planner (KAN-812, decision 18).
    job.answered_at, job.answered_by, job.updated_by = now, actor_id, actor_id
    db.add(
        ServiceOrderJobAnswer(
            tenant_id=order.tenant_id,
            service_order_id=order.id,
            job_id=job.id,
            result=data.result,
            channel=channel,
            note=note,
            amount_net=amount.net if amount else None,
            amount_gross=amount.gross if amount else None,
            hours_billed=hours_billed,
            recorded_at=now,
            recorded_by=actor_id,
            created_by=actor_id,
            updated_by=actor_id,
        )
    )
    record_order_change(
        db, order, action="service_order_job_answered", actor_id=actor_id, before=before,
        after={
            **_snapshot(job),
            "result": data.result.value,
            "channel": channel.value,
            "note": note,
            "amountNet": str(amount.net) if amount else None,
            "amountGross": str(amount.gross) if amount else None,
            "orderState": order.state.value,
            "peopleTold": [person.label for person in told],
        },
        reason=note,
    )


def record_answers(
    db: Session, *, order: ServiceOrder, actor_id: uuid.UUID, channel: AnswerChannel, answers: list[JobAnswerInput]
) -> None:
    """D8 «Antwort erfassen»: the customer's answers to one or more jobs of the order, as
    they came (desk, phone, message). Whole or not at all. Commits."""

    refuse_frozen(order)
    if not answers:
        raise UnprocessableEntityError(
            "Record an answer for at least one job.", details={"reason": "service_order_answer_empty"}
        )
    job_ids = [a.job_id for a in answers]
    if len(set(job_ids)) != len(job_ids):
        raise UnprocessableEntityError(
            "Each job is answered once per entry.", details={"reason": "service_order_answer_job_twice"}
        )
    jobs = {
        job.id: job
        for job in db.scalars(
            select(ServiceOrderJob).where(ServiceOrderJob.service_order_id == order.id, ServiceOrderJob.id.in_(job_ids))
        )
    }
    now = utcnow()
    for data in answers:
        job = jobs.get(data.job_id)
        if job is None:
            raise NotFoundError(f"Job {data.job_id} was not found.")
        _answer(db, order=order, job=job, data=data, channel=channel, actor_id=actor_id, now=now)
    db.commit()


def accept_job(db: Session, *, order: ServiceOrder, job: ServiceOrderJob, actor_id: uuid.UUID) -> None:
    """«Accepted» at the desk for one job: `record_answers` with one answer. Commits."""

    record_answers(
        db, order=order, actor_id=actor_id, channel=AnswerChannel.DESK,
        answers=[JobAnswerInput(job_id=job.id, result=AnswerResult.ACCEPTED)],
    )
