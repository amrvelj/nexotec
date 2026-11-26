import { useState, type ReactNode } from 'react'
import { ActionIcon, Button, Group, Paper, Stack, Table, Text, Tooltip } from '@mantine/core'
import { FileText, Pencil, Plus, Tag, Trash2, Wrench } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge, type BadgeTone } from '@nexotec/ui-kit'
import { api } from '../../../../api/client'
import type {
  JobAnswerState,
  ServiceOrderJobRead,
  ServiceOrderPositionRead,
  ServiceOrderRead,
} from '../../../../api/types'
import { formatCurrencyChf } from '../../../../utils/format'
import { decimal, formatLabourTime } from '../../serviceOrderOptions'
import { ConfirmDeleteDialog, JobNameDialog } from './OrderDialogs'
import { OrderTotalsCard } from './OrderTotalsCard'
import { PositionDialog } from './PositionDialog'

const ANSWER_TONE: Record<JobAnswerState, BadgeTone> = {
  draft: 'slate',
  waiting: 'warning',
  accepted: 'success',
  declined: 'destructive',
}

// Table 3.2: a job may be deleted while draft, waiting or declined — mirrored to disable the
// action with its reason; the server decides (`service_order_job_accepted`).
const DELETABLE_JOB_STATES: ReadonlySet<JobAnswerState> = new Set(['draft', 'waiting', 'declined'])

type Pending =
  | { kind: 'renameJob'; job: ServiceOrderJobRead }
  | { kind: 'deleteJob'; job: ServiceOrderJobRead }
  | { kind: 'position'; jobId: string; position: ServiceOrderPositionRead | null }
  | { kind: 'deletePosition'; position: ServiceOrderPositionRead }

interface PositionsTabProps {
  order: ServiceOrderRead
  /** The role may write `service_orders`; without it every change is hidden (ADR-104). */
  canWrite: boolean
  /** Ready for billing or cancelled (table 3.1): changes are disabled with the reason. */
  frozen: boolean
  onOrder: (order: ServiceOrderRead) => void
  onReload: () => void
}

function IconAction({ label, disabledReason, onClick, children }: { label: string; disabledReason: string | null; onClick: () => void; children: ReactNode }) {
  return (
    <Tooltip label={disabledReason ?? label}>
      <ActionIcon variant="subtle" color="gray" aria-label={label} disabled={disabledReason !== null} onClick={onClick}>
        {children}
      </ActionIcon>
    </Tooltip>
  )
}

/**
 * D3 «Positionen» (KAN-810 criteria 2–4): each job as a card — its name, the
 * billing category «Kunde» as a badge, its answer state — with its positions
 * (kind · description · quantity with its unit · price net · net · incl. VAT,
 * every figure the server's), then «Auftragstotal je Zahler».
 */
export function PositionsTab({ order, canWrite, frozen, onOrder, onReload }: PositionsTabProps) {
  const { t } = useTranslation()
  const [pending, setPending] = useState<Pending | null>(null)
  const frozenReason = frozen ? t('serviceOrder.actions.frozen') : null
  const ifMatch = { 'If-Match': String(order.version) }

  const done = (updated: ServiceOrderRead) => {
    onOrder(updated)
    setPending(null)
  }

  const quantity = (position: ServiceOrderPositionRead) => {
    if (position.kind === 'text') return ''
    if (position.kind === 'labour_fixed') return '1'
    return position.time ? formatLabourTime(t, position.time.quantity, position.time.unit) : '—'
  }

  const money = (value: string | null | undefined) => (value == null ? '' : formatCurrencyChf(decimal(value)))

  const jobCard = (job: ServiceOrderJobRead) => (
    <Paper key={job.id} withBorder radius="md" data-testid={`job-${job.id}`}>
      <Group justify="space-between" p="md" wrap="nowrap">
        <Text fw={600}>{job.name}</Text>
        <Group gap="xs" wrap="nowrap">
          <Badge tone="purple" icon={<Tag size={12} />}>
            {t(`serviceOrder.billingCategory.${job.billingCategory}`)}
          </Badge>
          <Badge tone={ANSWER_TONE[job.answerState]} dot>
            {t(`serviceOrder.answerState.${job.answerState}`)}
          </Badge>
          {canWrite && (
            <>
              <IconAction label={t('serviceOrder.jobs.rename')} disabledReason={frozenReason} onClick={() => setPending({ kind: 'renameJob', job })}>
                <Pencil size={16} />
              </IconAction>
              <IconAction
                label={t('serviceOrder.jobs.delete')}
                disabledReason={
                  frozenReason ?? (DELETABLE_JOB_STATES.has(job.answerState) ? null : t('apiRefusal.reasons.service_order_job_accepted'))
                }
                onClick={() => setPending({ kind: 'deleteJob', job })}
              >
                <Trash2 size={16} />
              </IconAction>
              <Tooltip label={frozenReason} disabled={frozenReason === null}>
                <span>
                  <Button
                    size="xs"
                    variant="default"
                    leftSection={<Plus size={14} />}
                    disabled={frozen}
                    onClick={() => setPending({ kind: 'position', jobId: job.id, position: null })}
                  >
                    {t('serviceOrder.jobs.addPosition')}
                  </Button>
                </span>
              </Tooltip>
            </>
          )}
        </Group>
      </Group>
      {job.positions.length === 0 ? (
        <Text size="sm" c="dimmed" px="md" pb="md">
          {t('serviceOrder.jobs.noPositions')}
        </Text>
      ) : (
        <Table verticalSpacing="xs" horizontalSpacing="md" withTableBorder={false}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t('serviceOrder.positions.columns.kind')}</Table.Th>
              <Table.Th>{t('serviceOrder.positions.columns.text')}</Table.Th>
              <Table.Th style={{ textAlign: 'right' }}>{t('serviceOrder.positions.columns.quantity')}</Table.Th>
              <Table.Th style={{ textAlign: 'right' }}>{t('serviceOrder.positions.columns.unitPriceNet')}</Table.Th>
              <Table.Th style={{ textAlign: 'right' }}>{t('serviceOrder.positions.columns.net')}</Table.Th>
              <Table.Th style={{ textAlign: 'right' }}>{t('serviceOrder.positions.columns.gross')}</Table.Th>
              {canWrite && <Table.Th />}
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {job.positions.map((position) => (
              <Table.Tr key={position.id} data-testid={`position-${position.id}`}>
                <Table.Td>
                  <Group gap={6} wrap="nowrap">
                    {position.kind === 'text' ? <FileText size={14} /> : <Wrench size={14} />}
                    <Text size="sm">{t(`serviceOrder.kind.${position.kind}`)}</Text>
                  </Group>
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{position.text}</Text>
                  {position.kind !== 'text' && (
                    <Text size="xs" c="dimmed">
                      {[
                        t(`serviceOrder.kindDetail.${position.kind}`),
                        position.labourCategoryLabel,
                        position.kind === 'labour_fixed' && position.time
                          ? formatLabourTime(t, position.time.quantity, position.time.unit)
                          : null,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{quantity(position)}</Table.Td>
                <Table.Td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{money(position.unitPrice?.net)}</Table.Td>
                <Table.Td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{money(position.lineNet)}</Table.Td>
                <Table.Td style={{ textAlign: 'right', whiteSpace: 'nowrap', fontWeight: 600 }}>{money(position.lineGross)}</Table.Td>
                {canWrite && (
                  <Table.Td style={{ whiteSpace: 'nowrap' }}>
                    <Group gap={2} wrap="nowrap" justify="flex-end">
                      <IconAction
                        label={t('serviceOrder.positions.edit')}
                        disabledReason={frozenReason}
                        onClick={() => setPending({ kind: 'position', jobId: job.id, position })}
                      >
                        <Pencil size={16} />
                      </IconAction>
                      <IconAction
                        label={t('serviceOrder.positions.delete')}
                        disabledReason={frozenReason}
                        onClick={() => setPending({ kind: 'deletePosition', position })}
                      >
                        <Trash2 size={16} />
                      </IconAction>
                    </Group>
                  </Table.Td>
                )}
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Paper>
  )

  return (
    <Stack gap="md">
      {order.jobs.length === 0 && (
        <Text size="sm" c="dimmed">
          {t('serviceOrder.jobs.empty')}
        </Text>
      )}
      {order.jobs.map(jobCard)}
      <OrderTotalsCard totals={order.totals} payerLabel={order.invoiceRecipient.label} />

      <JobNameDialog
        opened={pending?.kind === 'renameJob'}
        title={t('serviceOrder.jobs.dialog.renameTitle')}
        submitLabel={t('common.save')}
        initialName={pending?.kind === 'renameJob' ? pending.job.name : ''}
        onClose={() => setPending(null)}
        onSave={async (name) => {
          if (pending?.kind !== 'renameJob') return
          done(await api.patch<ServiceOrderRead>(`/service-orders/${order.id}/jobs/${pending.job.id}`, { name }, ifMatch))
        }}
        onReload={onReload}
      />
      <ConfirmDeleteDialog
        opened={pending?.kind === 'deleteJob'}
        title={t('serviceOrder.jobs.delete')}
        body={pending?.kind === 'deleteJob' ? t('serviceOrder.jobs.deleteBody', { name: pending.job.name }) : ''}
        onClose={() => setPending(null)}
        onConfirm={async () => {
          if (pending?.kind !== 'deleteJob') return
          done(await api.delete<ServiceOrderRead>(`/service-orders/${order.id}/jobs/${pending.job.id}`, ifMatch))
        }}
        onReload={onReload}
      />
      <ConfirmDeleteDialog
        opened={pending?.kind === 'deletePosition'}
        title={t('serviceOrder.positions.delete')}
        body={pending?.kind === 'deletePosition' ? t('serviceOrder.positions.deleteBody', { text: pending.position.text }) : ''}
        onClose={() => setPending(null)}
        onConfirm={async () => {
          if (pending?.kind !== 'deletePosition') return
          done(await api.delete<ServiceOrderRead>(`/service-orders/${order.id}/positions/${pending.position.id}`, ifMatch))
        }}
        onReload={onReload}
      />
      {pending?.kind === 'position' && (
        <PositionDialog
          opened
          order={order}
          jobId={pending.jobId}
          position={pending.position}
          onClose={() => setPending(null)}
          onSaved={done}
          onReload={onReload}
        />
      )}
    </Stack>
  )
}
