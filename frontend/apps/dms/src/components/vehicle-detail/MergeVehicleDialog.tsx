import { useEffect, useRef, useState } from 'react'
import { ArrowLeftRight, Search, TriangleAlert } from 'lucide-react'
import { Group, Loader, Modal, Stack, Text, TextInput, UnstyledButton } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useTranslation } from 'react-i18next'
import { purple, semantic, slate, white } from '@nexotec/ui-kit'
import { api, ApiError } from '../../api/client'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { formatDateOnly } from '../../utils/format'
import type { VehicleMdmDetailRead, VehicleSearchResult } from '../../api/types'

/** One car the search found: enough to recognise it before its record is read. */
interface Candidate {
  id: string
  vin: string
  vehicleNumber: string
}

interface MergeVehicleDialogProps {
  opened: boolean
  onClose: () => void
  /** The record the dialog was opened on. */
  vehicle: VehicleMdmDetailRead
  /** The merge went through: the record that stays. */
  onMerged: (survivorId: string) => void
}

/** The search's answers as one list: the resolved hit, a picker's cars, the filtered page — the open record left out. */
export function mergeCandidates(result: VehicleSearchResult, openId: string): Candidate[] {
  const all: Candidate[] = [
    ...(result.resolved ? [result.resolved] : []),
    ...result.pickerCandidates,
    ...result.filtered.items,
  ].map(({ id, vin, vehicleNumber }) => ({ id, vin, vehicleNumber }))
  const seen = new Set<string>([openId])
  return all.filter((c) => (seen.has(c.id) ? false : (seen.add(c.id), true)))
}

/**
 * KAN-524 (FR-V-12) — «Zusammenführen mit…». Two records of one car (usually
 * a VIN typo made a second one) become one, for good: there is no unmerge,
 * and every Betrieb shares both records. So this is a modal (UI/UX
 * Specification: modals are for irreversible actions), the two records are
 * shown side by side, the consequences are said plainly, and the user types
 * the vehicle number of the record that goes before the red button
 * («Endgültig zusammenführen») unlocks.
 *
 * By default the open record goes and the record found stays; «Richtung
 * tauschen» turns it round. Customers' links and Stock's cars follow the
 * merge on their own (the server's consumers).
 */
export function MergeVehicleDialog({ opened, onClose, vehicle, onMerged }: MergeVehicleDialogProps) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, 300)
  const [results, setResults] = useState<Candidate[]>([])
  const [searching, setSearching] = useState(false)
  const [searchFailed, setSearchFailed] = useState(false)

  const [other, setOther] = useState<VehicleMdmDetailRead | null>(null)
  const [loadingOther, setLoadingOther] = useState(false)
  const [otherFailed, setOtherFailed] = useState(false)
  // The pick the latest read is for: an answer for an earlier pick never lands beside a later one.
  const otherRequest = useRef<string | null>(null)
  const [openRecordStays, setOpenRecordStays] = useState(false)

  const [typed, setTyped] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // A retried merge replays the first answer instead of a 409.
  const idempotency = useIdempotencyKey()

  useEffect(() => {
    if (opened) return
    idempotency.renew()
    setQuery('')
    setResults([])
    setSearchFailed(false)
    setOther(null)
    setOtherFailed(false)
    otherRequest.current = null
    setOpenRecordStays(false)
    setTyped('')
    setError(null)
  }, [opened, idempotency])

  useEffect(() => {
    if (debouncedQuery.trim().length < 3) {
      setResults([])
      setSearchFailed(false)
      return
    }
    let cancelled = false
    setSearching(true)
    setSearchFailed(false)
    api
      .get<VehicleSearchResult>(`/vehicle-mdm/search?q=${encodeURIComponent(debouncedQuery.trim())}&limit=8`)
      .then((res) => {
        if (!cancelled) setResults(mergeCandidates(res, vehicle.id))
      })
      .catch(() => {
        // A failed search is an error, never "no matches" (ADR-035).
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
  }, [debouncedQuery, vehicle.id])

  const pick = async (candidate: Candidate) => {
    otherRequest.current = candidate.id
    setOther(null)
    setOtherFailed(false)
    setTyped('')
    setError(null)
    setLoadingOther(true)
    try {
      const record = await api.get<VehicleMdmDetailRead>(`/vehicle-mdm/${candidate.id}`)
      if (otherRequest.current === candidate.id) setOther(record)
    } catch {
      if (otherRequest.current === candidate.id) setOtherFailed(true)
    } finally {
      if (otherRequest.current === candidate.id) setLoadingOther(false)
    }
  }

  const duplicate = other && (openRecordStays ? other : vehicle)
  const survivor = other && (openRecordStays ? vehicle : other)
  const confirmed = duplicate !== null && typed.trim().toUpperCase() === duplicate.vehicleNumber.toUpperCase()

  // Every refusal carries `details.reason` (or the If-Match conflict's
  // `currentVersion`) and is said in the user's language — never the API's
  // English message.
  const refusal = (err: unknown): string => {
    if (!(err instanceof ApiError)) return t('vehicleDetail.mergeDialog.refused.generic')
    if (err.code === 'unknown_error') return t('common.requestFailed')
    const details = err.details ?? {}
    if (err.status === 409 && details.currentVersion != null) return t('vehicleDetail.mergeDialog.refused.staleVersion')
    switch (details.reason) {
      case 'vehicle_merge_same_record':
        return t('vehicleDetail.mergeDialog.refused.sameRecord')
      case 'vehicle_merged':
        return t('vehicleDetail.mergeDialog.refused.alreadyMerged')
      case 'vehicle_merge_both_in_stock':
        return t('vehicleDetail.mergeDialog.refused.bothInStock', {
          duplicate: String(details.duplicateStockNumber ?? '—'),
          survivor: String(details.survivorStockNumber ?? '—'),
        })
      default:
        if (err.status === 403) return t('apiRefusal.forbidden')
        if (err.status === 404) return t('apiRefusal.notFound')
        return t('vehicleDetail.mergeDialog.refused.generic')
    }
  }

  const submit = async () => {
    if (!duplicate || !survivor || !confirmed) return
    setSubmitting(true)
    setError(null)
    try {
      await api.post(
        `/vehicle-mdm/${duplicate.id}/merge`,
        { survivorId: survivor.id, survivorVersion: survivor.version },
        { 'If-Match': String(duplicate.version), ...idempotency.headers() },
      )
      idempotency.renew()
      onMerged(survivor.id)
    } catch (err) {
      setError(refusal(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title={t('vehicleDetail.mergeDialog.title')} size="lg">
      {!other ? (
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            {t('vehicleDetail.mergeDialog.intro', { vehicleNumber: vehicle.vehicleNumber })}
          </Text>
          <TextInput
            leftSection={<Search size={16} />}
            label={t('vehicleDetail.mergeDialog.searchLabel')}
            placeholder={t('vehicleDetail.mergeDialog.searchPlaceholder')}
            value={query}
            onChange={(e) => setQuery(e.currentTarget.value)}
            autoFocus
          />
          {(searching || loadingOther) && <Loader size="sm" mx="auto" my="sm" />}
          <Stack gap={4}>
            {results.map((c) => (
              <UnstyledButton
                key={c.id}
                onClick={() => void pick(c)}
                style={{ display: 'flex', flexDirection: 'column', padding: '8px 10px', borderRadius: 8, border: `1px solid ${slate[2]}` }}
              >
                <Text size="sm" fw={600}>
                  {c.vin}
                </Text>
                <Text size="xs" c="dimmed">
                  {c.vehicleNumber}
                </Text>
              </UnstyledButton>
            ))}
            {!searching && searchFailed && (
              <Text size="sm" c={semantic.destructive.text} ta="center" py="sm" role="alert">
                {t('vehicleDetail.mergeDialog.searchFailed')}
              </Text>
            )}
            {otherFailed && (
              <Text size="sm" c={semantic.destructive.text} ta="center" py="sm" role="alert">
                {t('vehicleDetail.mergeDialog.recordFailed')}
              </Text>
            )}
            {!searching && !searchFailed && debouncedQuery.trim().length >= 3 && results.length === 0 && (
              <Text size="sm" c="dimmed" ta="center" py="sm">
                {t('vehicleDetail.mergeDialog.noMatches')}
              </Text>
            )}
          </Stack>
        </Stack>
      ) : (
        <Stack gap="md">
          <Group justify="space-between">
            <UnstyledButton onClick={() => setOther(null)} style={{ fontSize: 13, color: purple[6], fontWeight: 600 }}>
              {t('vehicleDetail.mergeDialog.chooseDifferent')}
            </UnstyledButton>
            <UnstyledButton
              onClick={() => {
                setOpenRecordStays((stays) => !stays)
                setTyped('')
              }}
              style={{ fontSize: 13, color: purple[6], fontWeight: 600, display: 'flex', alignItems: 'center', gap: 6 }}
            >
              <ArrowLeftRight size={14} />
              {t('vehicleDetail.mergeDialog.swap')}
            </UnstyledButton>
          </Group>

          <Group grow align="stretch">
            <RecordColumn title={t('vehicleDetail.mergeDialog.goes')} record={duplicate!} tone="destructive" locale={locale} />
            <RecordColumn title={t('vehicleDetail.mergeDialog.stays')} record={survivor!} tone="success" locale={locale} />
          </Group>

          <div style={{ backgroundColor: semantic.warning.surface, border: `1px solid ${semantic.warning.border}`, borderRadius: 8, padding: 12 }}>
            <Group gap={6} mb={6}>
              <TriangleAlert size={16} color={semantic.warning.text} />
              <Text size="sm" fw={600} c={semantic.warning.text}>
                {t('vehicleDetail.mergeDialog.irreversibleTitle')}
              </Text>
            </Group>
            <Text size="xs" c={semantic.warning.text}>
              {t('vehicleDetail.mergeDialog.irreversibleBody', {
                goes: duplicate!.vehicleNumber,
                stays: survivor!.vehicleNumber,
              })}
            </Text>
            <TextInput
              mt={8}
              label={t('vehicleDetail.mergeDialog.typeToConfirm', { vehicleNumber: duplicate!.vehicleNumber })}
              value={typed}
              onChange={(e) => setTyped(e.currentTarget.value)}
              autoComplete="off"
            />
          </div>

          {error && (
            <Text size="sm" c={semantic.destructive.text} role="alert">
              {error}
            </Text>
          )}

          <Group justify="flex-end">
            <UnstyledButton onClick={onClose} style={{ fontSize: 14, fontWeight: 600, color: slate[6], padding: '8px 14px' }}>
              {t('common.cancel')}
            </UnstyledButton>
            <button
              type="button"
              disabled={!confirmed || submitting}
              onClick={() => void submit()}
              style={{
                fontSize: 14,
                fontWeight: 600,
                color: white,
                backgroundColor: confirmed ? semantic.destructive.text : slate[3],
                border: 'none',
                borderRadius: 8,
                padding: '8px 16px',
                cursor: confirmed && !submitting ? 'pointer' : 'not-allowed',
              }}
            >
              {submitting ? t('vehicleDetail.mergeDialog.submitting') : t('vehicleDetail.mergeDialog.submit')}
            </button>
          </Group>
        </Stack>
      )}
    </Modal>
  )
}

function RecordColumn({
  title,
  record,
  tone,
  locale,
}: {
  title: string
  record: VehicleMdmDetailRead
  tone: 'destructive' | 'success'
  locale: string
}) {
  const { t } = useTranslation()
  const colours = semantic[tone]
  return (
    <div style={{ border: `1px solid ${colours.border}`, backgroundColor: colours.surface, borderRadius: 8, padding: 12 }}>
      <Text size="xs" fw={700} c={colours.text} tt="uppercase" mb={6}>
        {title}
      </Text>
      <Stack gap={4}>
        <Text size="sm" fw={600}>
          {record.vin}
        </Text>
        <Text size="xs">
          {t('vehicleDetail.identity.vehicleNumber')}: {record.vehicleNumber}
        </Text>
        <Text size="xs">
          {t('vehicleDetail.identity.stammnummer')}: {record.stammnummer ?? '—'}
        </Text>
        <Text size="xs">
          {t('vehicleDetail.identity.firstRegistrationDate')}:{' '}
          {record.firstRegistrationDate ? formatDateOnly(record.firstRegistrationDate, locale) : '—'}
        </Text>
        {record.stockItem && (
          <Text size="xs">
            {t('vehicleDetail.mergeDialog.inYourStock', { stockNumber: record.stockItem.stockNumber ?? '—' })}
          </Text>
        )}
      </Stack>
    </div>
  )
}
