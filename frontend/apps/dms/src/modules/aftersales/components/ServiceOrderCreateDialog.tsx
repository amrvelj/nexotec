import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Alert, Anchor, Button, Group, Select, Stack, Text, UnstyledButton } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useQuery } from '@tanstack/react-query'
import { Car, Plus, User } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { FormDialog, Picker, radius, slate, spacing, type PickerRow } from '@nexotec/ui-kit'
import { api } from '../../../api/client'
import type {
  ContactPage,
  ContactVehiclePage,
  LocationList,
  ServiceOrderCreate,
  ServiceOrderRead,
  VehicleMdmRead,
  VehiclePartyAllocationRead,
  VehicleSearchResult,
} from '../../../api/types'
import { useAuth } from '../../../auth/AuthContext'
import { useIdempotencyKey } from '../../../hooks/useIdempotencyKey'
import { contactName } from '../../../shared/displayNames'
import { apiRefusalText } from '../../../utils/apiRefusal'
import { VehicleCreateDialog } from '../../vehicle'
import { ContactChooseDialog, type ChosenContact } from './ContactChooseDialog'

interface ServiceOrderCreateDialogProps {
  opened: boolean
  onClose: () => void
  onCreated: (order: ServiceOrderRead) => void
}

interface ChosenVehicle {
  id: string
  label: string
}

type PartyRole = 'contact' | 'invoiceRecipient' | 'driver'

const SEARCH_LIMIT = '8'

function vehicleText(parts: (string | null | undefined)[]): string {
  return parts.filter((part): part is string => Boolean(part)).join(' · ')
}

/**
 * D2 «Neuer Auftrag» (KAN-810 criterion 1). One search field, «Kontrollschild,
 * Kontakt oder VIN», whose results mix vehicles (the vehicle register's one
 * search, `GET /v1/vehicle-mdm/search`) and contacts (`GET /v1/contacts`).
 * Picking a vehicle sets the car and proposes its current keeper (the vehicle's
 * party roles) as the ordering party; picking a contact sets the ordering party
 * and offers that contact's vehicles, or «Fahrzeug erfassen» through the
 * vehicle module's own create step. Then the order's heads: ordering party,
 * invoice recipient (the ordering party unless changed), driver (optional) and
 * the location (a choice only when the Betrieb has more than one active one).
 *
 * The server decides every rule: a «Gesperrt» contact (check 2), a car that is
 * not identified (ruling 36), a merged contact — each refusal is shown as its
 * sentence (`apiRefusalText`). One Idempotency-Key per submission: a retry
 * replays the first answer, never a second order.
 */
export function ServiceOrderCreateDialog({ opened, onClose, onCreated }: ServiceOrderCreateDialogProps) {
  const { t } = useTranslation()
  const { activeDealership } = useAuth()
  const dealershipId = activeDealership?.id ?? ''
  const idempotency = useIdempotencyKey()

  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, 250)
  const [vehicle, setVehicle] = useState<ChosenVehicle | null>(null)
  const [contact, setContact] = useState<ChosenContact | null>(null)
  const [invoiceRecipient, setInvoiceRecipient] = useState<ChosenContact | null>(null)
  const [driver, setDriver] = useState<ChosenContact | null>(null)
  const [locationId, setLocationId] = useState<string | null>(null)
  const [holderNote, setHolderNote] = useState<'proposed' | 'none' | 'error' | null>(null)
  const [choosing, setChoosing] = useState<PartyRole | null>(null)
  const [vehicleCreateOpen, setVehicleCreateOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [missing, setMissing] = useState(false)

  // The vehicle whose keeper is being read; anything else the advisor does cancels the proposal.
  const proposalFor = useRef<string | null>(null)
  useEffect(() => {
    if (!opened) return
    idempotency.renew()
    setQuery('')
    setVehicle(null)
    setContact(null)
    setInvoiceRecipient(null)
    setDriver(null)
    setLocationId(null)
    setHolderNote(null)
    setError(null)
    setMissing(false)
    proposalFor.current = null
  }, [opened, idempotency])

  const searching = opened && vehicle === null && contact === null
  const q = debouncedQuery.trim()
  const vehicleSearch = useQuery({
    queryKey: ['service-order-create', 'vehicles', q],
    queryFn: () => api.get<VehicleSearchResult>(`/vehicle-mdm/search?${new URLSearchParams({ q, limit: SEARCH_LIMIT }).toString()}`),
    enabled: searching && q.length > 0,
  })
  const contactSearch = useQuery({
    queryKey: ['service-order-create', 'contacts', q],
    queryFn: () => api.get<ContactPage>(`/contacts?${new URLSearchParams({ q, limit: SEARCH_LIMIT }).toString()}`),
    enabled: searching && q.length > 0,
  })
  const contactVehicles = useQuery({
    queryKey: ['service-order-create', 'contact-vehicles', contact?.id],
    queryFn: () => api.get<ContactVehiclePage>(`/contacts/${contact!.id}/vehicles`),
    enabled: opened && contact !== null && vehicle === null,
  })
  const locations = useQuery({
    queryKey: ['dealerships', dealershipId, 'locations', 'active'],
    queryFn: () => api.get<LocationList>(`/dealerships/${dealershipId}/locations?active=true`),
    enabled: opened && dealershipId !== '',
  })
  const activeLocations = useMemo(() => locations.data?.items ?? [], [locations.data])
  // One active location: the order is there, nothing to choose.
  const effectiveLocationId = activeLocations.length === 1 ? activeLocations[0].id : locationId

  const searchRows = useMemo((): PickerRow[] => {
    const rows: PickerRow[] = []
    const result = vehicleSearch.data
    if (result?.resolved) {
      const hit = result.resolved
      rows.push({
        id: `v:${hit.id}`,
        identifier: hit.currentPlate ?? hit.vin,
        label: hit.currentPlate ? hit.vin : t('serviceOrders.create.vehicleRow', { vehicleNumber: hit.vehicleNumber }),
        sublabel: hit.currentPlate ? t('serviceOrders.create.vehicleRow', { vehicleNumber: hit.vehicleNumber }) : undefined,
      })
    }
    for (const candidate of result?.pickerCandidates ?? []) {
      rows.push({
        id: `v:${candidate.id}`,
        identifier: candidate.plate ?? candidate.vin,
        label: candidate.vin,
        sublabel: t('serviceOrders.create.vehicleRow', { vehicleNumber: candidate.vehicleNumber }),
      })
    }
    for (const item of result?.filtered.items ?? []) {
      rows.push({ id: `v:${item.id}`, identifier: item.vin, label: t('serviceOrders.create.vehicleRow', { vehicleNumber: item.vehicleNumber }) })
    }
    for (const found of contactSearch.data?.items ?? []) {
      rows.push({
        id: `c:${found.id}`,
        label: contactName(found),
        sublabel: t('serviceOrders.create.contactRow', { contactNumber: found.contactNumber }),
      })
    }
    return rows
  }, [vehicleSearch.data, contactSearch.data, t])

  // A picked vehicle proposes its current keeper («Halter») as the ordering party — unless the
  // advisor chose one, or started over, while the keeper was being read.
  const proposeHolder = async (vehicleId: string) => {
    proposalFor.current = vehicleId
    try {
      const parties = await api.get<VehiclePartyAllocationRead[]>(`/vehicle-mdm/${vehicleId}/party-roles`)
      if (proposalFor.current !== vehicleId) return
      const keeper = parties.find((party) => party.role === 'keeper' && party.effectiveTo === null)
      if (keeper) {
        setContact({ id: keeper.contactId, label: keeper.displayName?.trim() || t('serviceOrders.create.unnamedContact') })
        setHolderNote('proposed')
      } else {
        setHolderNote('none')
      }
    } catch {
      if (proposalFor.current === vehicleId) setHolderNote('error')
    } finally {
      if (proposalFor.current === vehicleId) proposalFor.current = null
    }
  }

  const pickRow = (row: PickerRow) => {
    const [kind, id] = [row.id.slice(0, 1), row.id.slice(2)]
    if (kind === 'v') {
      setVehicle({ id, label: vehicleText([row.identifier, row.label]) })
      void proposeHolder(id)
    } else {
      proposalFor.current = null
      setContact({ id, label: row.label })
    }
  }

  const pickVehicle = (picked: VehicleMdmRead | { id: string; label: string }) => {
    if ('vin' in picked) setVehicle({ id: picked.id, label: vehicleText([picked.vin, picked.vehicleNumber]) })
    else setVehicle(picked)
  }

  const startOver = () => {
    proposalFor.current = null
    setQuery('')
    setVehicle(null)
    setContact(null)
    setInvoiceRecipient(null)
    setDriver(null)
    setHolderNote(null)
    setError(null)
    setMissing(false)
  }

  const submit = async () => {
    if (!vehicle || !contact || !effectiveLocationId) {
      setMissing(true)
      return
    }
    setMissing(false)
    setSubmitting(true)
    setError(null)
    try {
      const body: ServiceOrderCreate = {
        locationId: effectiveLocationId,
        contactId: contact.id,
        vehicleId: vehicle.id,
        // Left out, the invoice recipient is the ordering party (the server's default).
        ...(invoiceRecipient ? { invoiceRecipientContactId: invoiceRecipient.id } : {}),
        ...(driver ? { driverContactId: driver.id } : {}),
      }
      const created = await api.post<ServiceOrderRead>('/service-orders', body, idempotency.headers(body))
      idempotency.renew()
      onCreated(created)
    } catch (err) {
      setError(err)
    } finally {
      setSubmitting(false)
    }
  }

  const roleTitle: Record<PartyRole, string> = {
    contact: t('serviceOrder.overview.contact'),
    invoiceRecipient: t('serviceOrder.overview.invoiceRecipient'),
    driver: t('serviceOrder.overview.driver'),
  }

  const headRow = (role: PartyRole, value: ChosenContact | null, shown: string | null, extra?: ReactNode) => (
    <Group justify="space-between" wrap="nowrap" key={role}>
      <Stack gap={0}>
        <Text size="xs" c="dimmed">
          {role === 'driver' ? t('serviceOrder.overview.driverOptional') : roleTitle[role]}
        </Text>
        <Text size="sm" fw={500} data-testid={`head-${role}`}>
          {shown ?? value?.label ?? t('common.notSet')}
        </Text>
      </Stack>
      <Group gap="xs" wrap="nowrap">
        {extra}
        <Button size="xs" variant="default" onClick={() => setChoosing(role)} aria-label={t('serviceOrder.overview.changeTitle', { role: roleTitle[role] })}>
          {t('serviceOrder.overview.change')}
        </Button>
      </Group>
    </Group>
  )

  const searchFailed = vehicleSearch.isError || contactSearch.isError

  return (
    <>
      <FormDialog
        opened={opened && choosing === null && !vehicleCreateOpen}
        onClose={onClose}
        title={t('serviceOrders.create.title')}
        onSubmit={submit}
        submitLabel={t('serviceOrders.create.submit')}
        cancelLabel={t('common.cancel')}
        submitting={submitting}
        size="lg"
      >
        {vehicle === null && contact === null && (
          <Stack gap="xs">
            <Text size="xs" fw={600} c="dimmed" tt="uppercase">
              {t('serviceOrders.create.searchLabel')}
            </Text>
            <Picker
              rows={searchRows}
              query={query}
              onQueryChange={setQuery}
              onSelect={pickRow}
              loading={vehicleSearch.isFetching || contactSearch.isFetching}
              inputLabel={t('serviceOrders.create.searchLabel')}
              placeholder={t('serviceOrders.create.searchPlaceholder')}
              emptyLabel={q ? t('serviceOrders.create.noMatches') : t('serviceOrders.create.typeToSearch')}
              onCreateNew={() => setVehicleCreateOpen(true)}
              createLabel={t('serviceOrders.create.createVehicle')}
            />
            {searchFailed && (
              <Text size="sm" c="red" role="alert">
                {t('serviceOrders.create.searchError')}
              </Text>
            )}
          </Stack>
        )}

        {vehicle === null && contact !== null && (
          <Stack gap="xs">
            <Text size="sm" fw={600}>
              {t('serviceOrders.create.contactVehicles', { name: contact.label })}
            </Text>
            {(contactVehicles.data?.items ?? [])
              .filter((row) => row.effectiveTo === null)
              .map((row) => {
                const name = [row.vehicle.make, row.vehicle.model].filter(Boolean).join(' ')
                const label = vehicleText([name || null, row.vehicle.vin, row.vehicle.vehicleNumber])
                return (
                  <UnstyledButton
                    key={row.id}
                    onClick={() => pickVehicle({ id: row.vehicleId, label })}
                    style={{ padding: spacing.sm, borderRadius: radius.sm, border: `1px solid ${slate[2]}` }}
                  >
                    <Group gap="xs" wrap="nowrap">
                      <Car size={16} />
                      <Text size="sm">{label}</Text>
                    </Group>
                  </UnstyledButton>
                )
              })}
            {contactVehicles.isSuccess && contactVehicles.data.items.filter((row) => row.effectiveTo === null).length === 0 && (
              <Text size="sm" c="dimmed">
                {t('serviceOrders.create.noContactVehicles')}
              </Text>
            )}
            <Group gap="sm">
              <Button size="xs" variant="default" leftSection={<Plus size={14} />} onClick={() => setVehicleCreateOpen(true)}>
                {t('serviceOrders.create.createVehicle')}
              </Button>
              <Anchor component="button" type="button" size="sm" onClick={startOver}>
                {t('serviceOrders.create.otherVehicle')}
              </Anchor>
            </Group>
          </Stack>
        )}

        {vehicle !== null && (
          <Stack gap="sm">
            <Group justify="space-between" wrap="nowrap">
              <Group gap="xs" wrap="nowrap">
                <Car size={16} />
                <Stack gap={0}>
                  <Text size="xs" c="dimmed">
                    {t('serviceOrders.create.vehicle')}
                  </Text>
                  <Text size="sm" fw={500} data-testid="head-vehicle">
                    {vehicle.label}
                  </Text>
                </Stack>
              </Group>
              <Anchor component="button" type="button" size="sm" onClick={startOver}>
                {t('serviceOrders.create.otherVehicle')}
              </Anchor>
            </Group>
            {holderNote === 'proposed' && (
              <Group gap={4}>
                <User size={14} color={slate[5]} />
                <Text size="xs" c="dimmed">
                  {t('serviceOrders.create.holderProposed')}
                </Text>
              </Group>
            )}
            {holderNote === 'none' && contact === null && (
              <Text size="xs" c="dimmed">
                {t('serviceOrders.create.noHolder')}
              </Text>
            )}
            {holderNote === 'error' && contact === null && (
              <Text size="xs" c="dimmed">
                {t('serviceOrders.create.holdersLoadError')}
              </Text>
            )}
            {headRow('contact', contact, null)}
            {headRow('invoiceRecipient', invoiceRecipient ?? contact, null)}
            {headRow(
              'driver',
              driver,
              null,
              driver ? (
                <Button size="xs" variant="subtle" color="gray" onClick={() => setDriver(null)}>
                  {t('serviceOrder.overview.removeDriver')}
                </Button>
              ) : undefined,
            )}
            {activeLocations.length > 1 && (
              <Select
                label={t('serviceOrders.create.location')}
                data={activeLocations.map((location) => ({ value: location.id, label: location.name }))}
                value={locationId}
                onChange={setLocationId}
                required
                allowDeselect={false}
              />
            )}
            {locations.isSuccess && activeLocations.length === 0 && (
              <Text size="sm" c="red">
                {t('serviceOrders.create.noLocation')}
              </Text>
            )}
          </Stack>
        )}

        {missing && (
          <Text size="sm" c="red" role="alert">
            {[
              !vehicle && t('serviceOrders.create.missing.vehicle'),
              !contact && t('serviceOrders.create.missing.contact'),
              !effectiveLocationId && t('serviceOrders.create.missing.location'),
            ]
              .filter(Boolean)
              .join(' ')}
          </Text>
        )}
        {error !== null && (
          <Alert color="red" role="alert">
            {apiRefusalText(t, error, 'serviceOrders.create.error')}
          </Alert>
        )}
      </FormDialog>

      <ContactChooseDialog
        opened={choosing !== null}
        title={choosing ? t('serviceOrder.overview.changeTitle', { role: roleTitle[choosing] }) : ''}
        onClose={() => setChoosing(null)}
        onChosen={(chosen) => {
          if (choosing === 'contact') {
            proposalFor.current = null
            setContact(chosen)
            // Another ordering party than the keeper: the «proposed» note no longer holds.
            setHolderNote(null)
          }
          if (choosing === 'invoiceRecipient') setInvoiceRecipient(chosen)
          if (choosing === 'driver') setDriver(chosen)
          setError(null)
          setChoosing(null)
        }}
      />

      <VehicleCreateDialog
        opened={vehicleCreateOpen}
        onClose={() => setVehicleCreateOpen(false)}
        onCreated={(created) => {
          setVehicleCreateOpen(false)
          pickVehicle(created)
          if (contact === null) void proposeHolder(created.id)
        }}
      />
    </>
  )
}
