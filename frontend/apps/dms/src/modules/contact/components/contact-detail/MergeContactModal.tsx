import { useEffect, useRef, useState } from 'react'
import { Building2, Search, TriangleAlert, User } from 'lucide-react'
import { Checkbox, Group, Loader, Modal, Stack, Text, TextInput, UnstyledButton } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { Trans, useTranslation } from 'react-i18next'
import { ContactTypeBadge, LifecycleStatusBadge, purple, semantic, slate, white } from '@nexotec/ui-kit'
import { api, ApiError } from '../../../../api/client'
import { useIdempotencyKey } from '../../../../hooks/useIdempotencyKey'
import { translatedContactTypeLabel, translatedLifecycleLabel } from '../../contactOptions'
import type { ContactEmailPage, ContactEmailRead, ContactPage, ContactPhonePage, ContactPhoneRead, ContactRead } from '../../../../api/types'

function contactLabel(c: ContactRead): string {
  return c.contactType === 'business' ? (c.companyName ?? '') : `${c.firstName ?? ''} ${c.lastName ?? ''}`.trim()
}

function primaryPhone(phones: ContactPhoneRead[]): string {
  return phones.find((p) => p.isPrimary)?.phoneE164 ?? phones[0]?.phoneE164 ?? '—'
}

function primaryEmail(emails: ContactEmailRead[]): string {
  return emails.find((e) => e.isPrimary)?.emailAddress ?? emails[0]?.emailAddress ?? '—'
}

interface MergeContactModalProps {
  opened: boolean
  onClose: () => void
  contact: ContactRead
  phones: ContactPhoneRead[]
  emails: ContactEmailRead[]
  onMerged: (survivorId: string) => void
}

/**
 * FR-09 + § UI/UX Core Principles: "Modals are reserved for irreversible
 * or destructive actions only — merge, anonymise, delete." This is the
 * first (and so far only) modal-based flow in the app for exactly that
 * reason — every other edit in the DMS is inline.
 *
 * `contact` is the duplicate — the one that gets marked `merged` and
 * whose vehicles, offers, contracts, contacts and external IDs are re-pointed away
 * from it. The record the user picks in this modal is the survivor.
 */
export function MergeContactModal({ opened, onClose, contact, phones, emails, onMerged }: MergeContactModalProps) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, 300)
  const [results, setResults] = useState<ContactRead[]>([])
  const [searching, setSearching] = useState(false)
  const [searchFailed, setSearchFailed] = useState(false)

  const [survivor, setSurvivor] = useState<ContactRead | null>(null)
  const [survivorPhones, setSurvivorPhones] = useState<ContactPhoneRead[]>([])
  const [survivorEmails, setSurvivorEmails] = useState<ContactEmailRead[]>([])
  const [loadingSurvivorContacts, setLoadingSurvivorContacts] = useState(false)
  const [survivorContactsFailed, setSurvivorContactsFailed] = useState(false)
  // The survivor whose contacts the latest load is for: an answer for an
  // earlier pick never lands beside a later one (KAN-299).
  const survivorRequest = useRef<string | null>(null)

  const [confirmed, setConfirmed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // KAN-266: a retried merge replays the first answer instead of a 409.
  const idempotency = useIdempotencyKey()

  useEffect(() => {
    if (opened) return
    idempotency.renew()
    setQuery('')
    setResults([])
    setSearchFailed(false)
    setSurvivor(null)
    setSurvivorPhones([])
    setSurvivorEmails([])
    setSurvivorContactsFailed(false)
    survivorRequest.current = null
    setConfirmed(false)
    setError(null)
  }, [opened, idempotency])

  useEffect(() => {
    if (debouncedQuery.trim().length < 2) {
      setResults([])
      setSearchFailed(false)
      return
    }
    let cancelled = false
    setSearching(true)
    setSearchFailed(false)
    api
      .get<ContactPage>(`/contacts?q=${encodeURIComponent(debouncedQuery)}&limit=8`)
      .then((res) => {
        if (!cancelled) setResults(res.items.filter((c) => c.id !== contact.id))
      })
      .catch(() => {
        // KAN-299 (ADR-035): a failed search is an error, never "No matches".
        if (cancelled) return
        setResults([])
        setSearchFailed(true)
      })
      .finally(() => {
        if (!cancelled) setSearching(false)
      })
    return () => {
      cancelled = true
    }
  }, [debouncedQuery, contact.id])

  // KAN-299 — the compare columns are what the user checks before an
  // irreversible merge: a new pick starts with no contacts (never the
  // previous survivor's), and a failed load says so instead of rejecting
  // unhandled.
  const selectSurvivor = async (candidate: ContactRead) => {
    survivorRequest.current = candidate.id
    setSurvivor(candidate)
    setSurvivorPhones([])
    setSurvivorEmails([])
    setSurvivorContactsFailed(false)
    setError(null)
    setLoadingSurvivorContacts(true)
    try {
      const [phonesPage, emailsPage] = await Promise.all([
        api.get<ContactPhonePage>(`/contacts/${candidate.id}/phones`),
        api.get<ContactEmailPage>(`/contacts/${candidate.id}/emails`),
      ])
      if (survivorRequest.current !== candidate.id) return
      setSurvivorPhones(phonesPage.items)
      setSurvivorEmails(emailsPage.items)
    } catch {
      if (survivorRequest.current === candidate.id) setSurvivorContactsFailed(true)
    } finally {
      if (survivorRequest.current === candidate.id) setLoadingSurvivorContacts(false)
    }
  }

  // KAN-366: a refused merge is said in the user's language, keyed off
  // `details.reason` (or the If-Match conflict's `currentVersion`) — never
  // the API's English message. A response without the API's error body
  // (a gateway's 504, KAN-271) is a failed request, not a refusal; anything
  // else falls back to the translated generic.
  const mergeRefusalMessage = (err: unknown): string => {
    if (!(err instanceof ApiError)) return t('contactDetail.merge.error')
    if (err.code === 'unknown_error') return t('common.requestFailed')
    const details = err.details ?? {}
    if (err.status === 409 && details.currentVersion != null) return t('contactDetail.mergeRefused.staleVersion')
    switch (details.reason) {
      case 'merge_into_self':
        return t('contactDetail.mergeRefused.intoSelf')
      case 'merge_target_merged':
        return t('contactDetail.mergeRefused.targetMerged')
      case 'contact_already_merged':
        return t('contactDetail.mergeRefused.alreadyMerged')
      case 'merge_conflict_on_survivor':
        return t('contactDetail.mergeRefused.survivorConflict')
      case 'merge_vehicle_party_busy':
        return t('contactDetail.mergeRefused.vehiclePartyBusy')
      case 'customer_role_not_permitted':
        return t('contactDetail.mergeRefused.customerRoleNotPermitted')
      default:
        return t('contactDetail.merge.error')
    }
  }

  const submit = async () => {
    if (!survivor) return
    setSubmitting(true)
    setError(null)
    try {
      await api.post(
        `/contacts/${contact.id}/merge`,
        { duplicateOfContactId: survivor.id },
        { 'If-Match': String(contact.version), ...idempotency.headers() },
      )
      idempotency.renew()
      onMerged(survivor.id)
    } catch (err) {
      setError(mergeRefusalMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title={t('contactDetail.merge.title')} size="lg">
      {!survivor ? (
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            {/* The name is the element's own child, not an interpolated value:
                <Trans> would parse markup inside an interpolated name. */}
            <Trans
              i18nKey="contactDetail.merge.intro"
              components={{ name: <strong>{contactLabel(contact) || contact.contactNumber}</strong> }}
            />
          </Text>
          <TextInput
            leftSection={<Search size={16} />}
            placeholder={t('contactDetail.merge.searchPlaceholder')}
            value={query}
            onChange={(e) => setQuery(e.currentTarget.value)}
            autoFocus
          />
          {searching && <Loader size="sm" mx="auto" my="sm" />}
          <Stack gap={4}>
            {results.map((c) => (
              <UnstyledButton
                key={c.id}
                onClick={() => void selectSurvivor(c)}
                style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px', borderRadius: 8, border: `1px solid ${slate[2]}` }}
              >
                <Group gap={8}>
                  {c.contactType === 'business' ? <Building2 size={16} /> : <User size={16} />}
                  <Stack gap={0}>
                    <Text size="sm" fw={600}>
                      {contactLabel(c) || c.contactNumber}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {c.contactNumber}
                    </Text>
                  </Stack>
                </Group>
                <LifecycleStatusBadge status={c.lifecycleStatus} label={translatedLifecycleLabel(t, c.lifecycleStatus)} />
              </UnstyledButton>
            ))}
            {!searching && searchFailed && (
              <Text size="sm" c={semantic.destructive.text} ta="center" py="sm" role="alert">
                {t('contactDetail.merge.searchFailed')}
              </Text>
            )}
            {!searching && !searchFailed && debouncedQuery.trim().length >= 2 && results.length === 0 && (
              <Text size="sm" c="dimmed" ta="center" py="sm">
                {t('contactDetail.merge.noMatches')}
              </Text>
            )}
          </Stack>
        </Stack>
      ) : (
        <Stack gap="md">
          <UnstyledButton onClick={() => setSurvivor(null)} style={{ fontSize: 13, color: purple[6], fontWeight: 600 }}>
            {t('contactDetail.merge.chooseDifferent')}
          </UnstyledButton>

          {loadingSurvivorContacts ? (
            <Loader size="sm" mx="auto" />
          ) : survivorContactsFailed ? (
            <Text size="sm" c={semantic.destructive.text} role="alert">
              {t('contactDetail.merge.survivorContactsFailed')}
            </Text>
          ) : (
            <Group grow align="stretch">
              <CompareColumn title={t('contactDetail.merge.duplicateColumn')} contact={contact} phones={phones} emails={emails} tone="destructive" />
              <CompareColumn title={t('contactDetail.merge.survivorColumn')} contact={survivor} phones={survivorPhones} emails={survivorEmails} tone="success" />
            </Group>
          )}

          <div style={{ backgroundColor: semantic.warning.surface, border: `1px solid ${semantic.warning.border}`, borderRadius: 8, padding: 12 }}>
            <Group gap={6} mb={6}>
              <TriangleAlert size={16} color={semantic.warning.text} />
              <Text size="sm" fw={600} c={semantic.warning.text}>
                {t('contactDetail.merge.irreversibleTitle')}
              </Text>
            </Group>
            <Text size="xs" c={semantic.warning.text}>
              {t('contactDetail.merge.irreversibleBody')}
            </Text>
            <Checkbox
              mt={8}
              label={t('contactDetail.merge.confirm')}
              checked={confirmed}
              onChange={(e) => setConfirmed(e.currentTarget.checked)}
            />
          </div>

          {error && (
            <Text size="sm" c={semantic.destructive.text}>
              {error}
            </Text>
          )}

          <Group justify="flex-end">
            <UnstyledButton onClick={onClose} style={{ fontSize: 14, fontWeight: 600, color: slate[6], padding: '8px 14px' }}>
              {t('common.cancel')}
            </UnstyledButton>
            <button
              type="button"
              // KAN-299 — the merge is irreversible: it waits until the
              // survivor's contacts are on screen to be compared.
              disabled={!confirmed || submitting || loadingSurvivorContacts || survivorContactsFailed}
              onClick={() => void submit()}
              style={{
                fontSize: 14,
                fontWeight: 600,
                color: white,
                backgroundColor: confirmed && !loadingSurvivorContacts && !survivorContactsFailed ? semantic.destructive.text : slate[3],
                border: 'none',
                borderRadius: 8,
                padding: '8px 16px',
                cursor: confirmed && !submitting && !loadingSurvivorContacts && !survivorContactsFailed ? 'pointer' : 'not-allowed',
              }}
            >
              {submitting ? t('contactDetail.merge.submitting') : t('contactDetail.merge.submit')}
            </button>
          </Group>
        </Stack>
      )}
    </Modal>
  )
}

function CompareColumn({
  title,
  contact,
  phones,
  emails,
  tone,
}: {
  title: string
  contact: ContactRead
  phones: ContactPhoneRead[]
  emails: ContactEmailRead[]
  tone: 'destructive' | 'success'
}) {
  const { t } = useTranslation()
  const toneColors = semantic[tone]
  return (
    <div style={{ border: `1px solid ${toneColors.border}`, backgroundColor: toneColors.surface, borderRadius: 8, padding: 12 }}>
      <Text size="xs" fw={700} c={toneColors.text} tt="uppercase" mb={6}>
        {title}
      </Text>
      <Stack gap={4}>
        <Group gap={6}>
          <ContactTypeBadge type={contact.contactType} label={translatedContactTypeLabel(t, contact.contactType)} />
          <Text size="sm" fw={600}>
            {contactLabel(contact) || contact.contactNumber}
          </Text>
        </Group>
        <Text size="xs" c="dimmed">
          {contact.contactNumber}
        </Text>
        <Text size="xs">{primaryPhone(phones)}</Text>
        <Text size="xs">{primaryEmail(emails)}</Text>
        <LifecycleStatusBadge status={contact.lifecycleStatus} label={translatedLifecycleLabel(t, contact.lifecycleStatus)} />
      </Stack>
    </div>
  )
}
