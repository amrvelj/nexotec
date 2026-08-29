import { useEffect, useState } from 'react'
import { Building2, Search, TriangleAlert, User } from 'lucide-react'
import { Checkbox, Group, Loader, Modal, Stack, Text, TextInput, UnstyledButton } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { CustomerTypeBadge, LifecycleStatusBadge, purple, semantic, slate, white } from '@nexotec/ui-kit'
import { api, ApiError } from '../../api/client'
import type { CustomerEmailPage, CustomerEmailRead, CustomerPage, CustomerPhonePage, CustomerPhoneRead, CustomerRead } from '../../api/types'

function customerLabel(c: CustomerRead): string {
  return c.customerType === 'business' ? (c.companyName ?? '') : `${c.firstName ?? ''} ${c.lastName ?? ''}`.trim()
}

function primaryPhone(phones: CustomerPhoneRead[]): string {
  return phones.find((p) => p.isPrimary)?.phoneE164 ?? phones[0]?.phoneE164 ?? '—'
}

function primaryEmail(emails: CustomerEmailRead[]): string {
  return emails.find((e) => e.isPrimary)?.emailAddress ?? emails[0]?.emailAddress ?? '—'
}

interface MergeCustomerModalProps {
  opened: boolean
  onClose: () => void
  customer: CustomerRead
  phones: CustomerPhoneRead[]
  emails: CustomerEmailRead[]
  onMerged: (survivorId: string) => void
}

/**
 * FR-09 + § UI/UX Core Principles: "Modals are reserved for irreversible
 * or destructive actions only — merge, anonymise, delete." This is the
 * first (and so far only) modal-based flow in the app for exactly that
 * reason — every other edit in the DMS is inline.
 *
 * `customer` is the duplicate — the one that gets marked `merged` and
 * whose vehicles/transactions/contacts/external IDs are re-pointed away
 * from it. The record the user picks in this modal is the survivor.
 */
export function MergeCustomerModal({ opened, onClose, customer, phones, emails, onMerged }: MergeCustomerModalProps) {
  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, 300)
  const [results, setResults] = useState<CustomerRead[]>([])
  const [searching, setSearching] = useState(false)

  const [survivor, setSurvivor] = useState<CustomerRead | null>(null)
  const [survivorPhones, setSurvivorPhones] = useState<CustomerPhoneRead[]>([])
  const [survivorEmails, setSurvivorEmails] = useState<CustomerEmailRead[]>([])
  const [loadingSurvivorContacts, setLoadingSurvivorContacts] = useState(false)

  const [confirmed, setConfirmed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (opened) return
    setQuery('')
    setResults([])
    setSurvivor(null)
    setConfirmed(false)
    setError(null)
  }, [opened])

  useEffect(() => {
    if (debouncedQuery.trim().length < 2) {
      setResults([])
      return
    }
    let cancelled = false
    setSearching(true)
    api
      .get<CustomerPage>(`/customers?q=${encodeURIComponent(debouncedQuery)}&limit=8`)
      .then((res) => {
        if (!cancelled) setResults(res.items.filter((c) => c.id !== customer.id))
      })
      .catch(() => {
        if (!cancelled) setResults([])
      })
      .finally(() => {
        if (!cancelled) setSearching(false)
      })
    return () => {
      cancelled = true
    }
  }, [debouncedQuery, customer.id])

  const selectSurvivor = async (candidate: CustomerRead) => {
    setSurvivor(candidate)
    setError(null)
    setLoadingSurvivorContacts(true)
    try {
      const [phonesPage, emailsPage] = await Promise.all([
        api.get<CustomerPhonePage>(`/customers/${candidate.id}/phones`),
        api.get<CustomerEmailPage>(`/customers/${candidate.id}/emails`),
      ])
      setSurvivorPhones(phonesPage.items)
      setSurvivorEmails(emailsPage.items)
    } finally {
      setLoadingSurvivorContacts(false)
    }
  }

  const submit = async () => {
    if (!survivor) return
    setSubmitting(true)
    setError(null)
    try {
      await api.post(`/customers/${customer.id}/merge`, { duplicateOfCustomerId: survivor.id }, { 'If-Match': String(customer.version) })
      onMerged(survivor.id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to merge.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title="Merge duplicate customer" size="lg">
      {!survivor ? (
        <Stack gap="sm">
          <Text size="sm" c="dimmed">
            Search for the customer <strong>{customerLabel(customer) || customer.customerNumber}</strong> should be merged into. That
            record survives; this one is marked merged.
          </Text>
          <TextInput
            leftSection={<Search size={16} />}
            placeholder="Search by name, email, phone, customer number…"
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
                  {c.customerType === 'business' ? <Building2 size={16} /> : <User size={16} />}
                  <Stack gap={0}>
                    <Text size="sm" fw={600}>
                      {customerLabel(c) || c.customerNumber}
                    </Text>
                    <Text size="xs" c="dimmed">
                      {c.customerNumber}
                    </Text>
                  </Stack>
                </Group>
                <LifecycleStatusBadge status={c.lifecycleStatus} />
              </UnstyledButton>
            ))}
            {!searching && debouncedQuery.trim().length >= 2 && results.length === 0 && (
              <Text size="sm" c="dimmed" ta="center" py="sm">
                No matches.
              </Text>
            )}
          </Stack>
        </Stack>
      ) : (
        <Stack gap="md">
          <UnstyledButton onClick={() => setSurvivor(null)} style={{ fontSize: 13, color: purple[6], fontWeight: 600 }}>
            ← Choose a different survivor
          </UnstyledButton>

          {loadingSurvivorContacts ? (
            <Loader size="sm" mx="auto" />
          ) : (
            <Group grow align="stretch">
              <CompareColumn title="This customer — will be merged" customer={customer} phones={phones} emails={emails} tone="destructive" />
              <CompareColumn title="Survivor — will remain" customer={survivor} phones={survivorPhones} emails={survivorEmails} tone="success" />
            </Group>
          )}

          <div style={{ backgroundColor: semantic.warning.surface, border: `1px solid ${semantic.warning.border}`, borderRadius: 8, padding: 12 }}>
            <Group gap={6} mb={6}>
              <TriangleAlert size={16} color={semantic.warning.text} />
              <Text size="sm" fw={600} c={semantic.warning.text}>
                This cannot be undone
              </Text>
            </Group>
            <Text size="xs" c={semantic.warning.text}>
              Vehicles, transactions, phones, emails and external IDs move to the survivor. This customer becomes read-only and is hidden
              from search.
            </Text>
            <Checkbox
              mt={8}
              label="I understand this is permanent"
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
              Cancel
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
              {submitting ? 'Merging…' : 'Merge customers'}
            </button>
          </Group>
        </Stack>
      )}
    </Modal>
  )
}

function CompareColumn({
  title,
  customer,
  phones,
  emails,
  tone,
}: {
  title: string
  customer: CustomerRead
  phones: CustomerPhoneRead[]
  emails: CustomerEmailRead[]
  tone: 'destructive' | 'success'
}) {
  const toneColors = semantic[tone]
  return (
    <div style={{ border: `1px solid ${toneColors.border}`, backgroundColor: toneColors.surface, borderRadius: 8, padding: 12 }}>
      <Text size="xs" fw={700} c={toneColors.text} tt="uppercase" mb={6}>
        {title}
      </Text>
      <Stack gap={4}>
        <Group gap={6}>
          <CustomerTypeBadge type={customer.customerType} />
          <Text size="sm" fw={600}>
            {customerLabel(customer) || customer.customerNumber}
          </Text>
        </Group>
        <Text size="xs" c="dimmed">
          {customer.customerNumber}
        </Text>
        <Text size="xs">{primaryPhone(phones)}</Text>
        <Text size="xs">{primaryEmail(emails)}</Text>
        <LifecycleStatusBadge status={customer.lifecycleStatus} />
      </Stack>
    </div>
  )
}
