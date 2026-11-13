import { useState } from 'react'
import { Alert, Badge, Button, Group, Select, Stack } from '@mantine/core'
import { CalendarX, UserPlus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge as KitBadge, FieldLabel, InlineEditField, KeyValueRow, OverviewCard, Picker, purple, RowMenu, useOverlay } from '@nexotec/ui-kit'
import { useInlineEditRefusal } from '../../hooks/useInlineEditRefusal'
import { toSwissLocale, type SupportedLanguage } from '../../i18n'
import { isStaleVersion } from '../../utils/apiRefusal'
import { formatDate, formatDateOnly } from '../../utils/format'
import { vehiclePartyRefusalMessage } from '../../utils/vehiclePartyRefusal'
import { isPlannedNeverStarted, isUpcoming } from '../../utils/vehicleParty'
import { CustomerDetailContent } from '../../pages/CustomerDetailPage'
import type { VehicleMdmRead, VehiclePartyAllocationRead, VehiclePartyRole } from '../../api/types'
import type { components } from '../../api/schema'

// KAN-667 — derived from the generated schema like every type in api/types.ts
// (move it there once that file is free to change).
type VehicleStockItemLink = components['schemas']['VehicleStockItemLink']

interface IdentityTabProps {
  vehicle: VehicleMdmRead
  parties: VehiclePartyAllocationRead[]
  formerParties: VehiclePartyAllocationRead[]
  onSaveField: (patch: Record<string, unknown>) => Promise<void>
  onReload: () => void
  onAllocate: (customerId: string, role: VehiclePartyRole) => Promise<void>
  /** The customer picker's open state, owned by the page: the header's
   * «Allocate to customer» opens it too (KAN-468). */
  allocating: boolean
  onAllocatingChange: (open: boolean) => void
  customerCandidates: { id: string; label: string; sublabel?: string }[]
  customerSearch: string
  onCustomerSearchChange: (q: string) => void
  /** U-11: fired when a customer overlay opened from a party row closes. */
  onCustomerOverlayClose: () => void
  /** KAN-583 (ruling 2A): «Eintrag beenden» in each party row's menu — ends a
   * current entry now, cancels a planned one. Offered only when the session
   * may write vehicle links (KAN-430). */
  canEndEntry?: boolean
  onEndEntry?: (party: VehiclePartyAllocationRead) => Promise<void>
  /** KAN-667 (Anto, 2026-11-13, option A): the stock car of the session's own
   * Betrieb that holds this record, or null. While there is one, the VIN is
   * read-only here: it is corrected on that car with «VIN korrigieren». */
  stockItem?: VehicleStockItemLink | null
  /** Opens that stock car at its «VIN korrigieren»; absent without the Stock
   * module (the VIN is then read-only with its hint, and no link). */
  onCorrectVinInStock?: () => void
}

const ROLES: VehiclePartyRole[] = ['owner', 'keeper', 'driver']

const PARTY_LINK_STYLE = { color: purple[6], background: 'none', border: 'none', cursor: 'pointer', padding: 0, textDecoration: 'underline' } as const

/**
 * FR-V-15: identity fields are editable ONLY here — inline, per the
 * "editing one value on a record already on screen is inline" rule.
 * FR-V-05/ADR-064: the "Allocate to customer" affordance is the vehicle-
 * side half of the one shared dialog (the customer-side half lives on
 * Customer 360's own Vehicles tab) — both call the same
 * app.customer.public.allocate_vehicle_party under the hood.
 */
export function IdentityTab({
  vehicle,
  parties,
  formerParties,
  onSaveField,
  onReload,
  onAllocate,
  allocating,
  onAllocatingChange: setAllocating,
  customerCandidates,
  customerSearch,
  onCustomerSearchChange,
  onCustomerOverlayClose,
  canEndEntry = false,
  onEndEntry,
  stockItem = null,
  onCorrectVinInStock,
}: IdentityTabProps) {
  const { t, i18n } = useTranslation()
  const locale = toSwissLocale(i18n.language as SupportedLanguage)
  const [allocationSaving, setAllocationSaving] = useState(false)
  const [allocationError, setAllocationError] = useState<string | null>(null)
  const [role, setRole] = useState<VehiclePartyRole>('owner')
  const [showFormer, setShowFormer] = useState(false)
  const [endingId, setEndingId] = useState<string | null>(null)
  const [endError, setEndError] = useState<string | null>(null)
  const { describeError, reloadLabel } = useInlineEditRefusal()

  const endEntry = async (party: VehiclePartyAllocationRead) => {
    if (!onEndEntry || endingId) return
    setEndingId(party.id)
    setEndError(null)
    try {
      await onEndEntry(party)
    } catch {
      setEndError(t('vehicleDetail.parties.endEntryError'))
    } finally {
      setEndingId(null)
    }
  }

  // KAN-644 (Anto, 2026-11-12, A): one current holder per role; a holder
  // whose start is still to come is listed after them as upcoming.
  const currentParties = parties.filter((p) => !isUpcoming(p))
  const upcomingParties = parties.filter((p) => isUpcoming(p))
  const partyMenu = (p: VehiclePartyAllocationRead) =>
    canEndEntry && onEndEntry ? (
      <RowMenu
        ariaLabel={t('vehicleDetail.parties.entryMenu')}
        groups={{
          destructive: [
            {
              label: t('vehicleDetail.parties.endEntry'),
              icon: <CalendarX size={16} />,
              onClick: () => void endEntry(p),
              disabled: endingId === p.id,
            },
          ],
        }}
      />
    ) : null
  const overlay = useOverlay()

  // KAN-269 (ADR-035): a failed allocation leaves the picker open with the
  // chosen role and search, says why, and stops showing it as busy — the
  // retry (same customer, same role) reuses its Idempotency-Key.
  const allocate = async (customerId: string) => {
    if (allocationSaving) return
    setAllocationSaving(true)
    setAllocationError(null)
    try {
      await onAllocate(customerId, role)
      setAllocating(false)
    } catch (err) {
      // KAN-389: said in the user's language, as the customer's Link vehicle
      // dialog says it (KAN-369) — never the API's English message.
      setAllocationError(vehiclePartyRefusalMessage(t, err, 'vehicleDetail.parties.allocateError'))
    } finally {
      setAllocationSaving(false)
    }
  }

  const closeAllocation = () => {
    setAllocating(false)
    setAllocationError(null)
  }

  // KAN-140: the holder's name, never the raw customer id (an internal id
  // is never user-visible text). displayName is optional on the contract,
  // so a row without one falls back to a translated label, not the UUID.
  const partyLabel = (p: VehiclePartyAllocationRead) => p.displayName || t('vehicleDetail.parties.unnamedCustomer')

  // § ADR-059 — "opening a record from inside a process renders it as an
  // overlay on top, not a navigation." Working on a vehicle's party
  // allocation and needing to check the customer behind a raw id is
  // exactly that process — this used to just print the id as plain text.
  // U-11 (KAN-140): the party rows show the holder's name, and the
  // overlaid Customer 360 can edit it inline — so closing the overlay hands
  // back to the page to refetch the party roles, or the card keeps the old
  // name until something unrelated refetches it.
  const openCustomerOverlay = (customerId: string) => {
    overlay.push({
      key: `customer-overlay-${customerId}`,
      content: <CustomerDetailContent customerId={customerId} embedded />,
      onClose: onCustomerOverlayClose,
    })
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
      <OverviewCard title={t('vehicleDetail.identity.title')}>
        <KeyValueRow label={t('vehicleDetail.identity.vin')}>
          {stockItem ? (
            // KAN-667: a car the Betrieb holds in stock keeps its VIN here — a
            // change here alone would leave the stock item on the old one.
            // The server refuses it too (409 vin_change_needs_vin_correction).
            <Stack gap={4} align="flex-start">
              <FieldLabel
                label={<span data-testid="vin-read-only" style={{ fontFamily: 'monospace' }}>{vehicle.vin}</span>}
                hint={t('vehicleDetail.identity.vinHeldInStock')}
                labels={{ icon: t('common.fieldHintIcon') }}
              />
              {onCorrectVinInStock && (
                <button type="button" style={PARTY_LINK_STYLE} onClick={onCorrectVinInStock}>
                  {stockItem.stockNumber
                    ? t('vehicleDetail.identity.correctVinInStock', { stockNumber: stockItem.stockNumber })
                    : t('vehicleDetail.identity.correctVinInStockUnnumbered')}
                </button>
              )}
            </Stack>
          ) : (
            <InlineEditField
              value={vehicle.vin}
              emptyLabel={t('common.notSet')}
              onSave={(v) => onSaveField({ vin: v.toUpperCase() })}
              describeError={describeError}
              isConflict={isStaleVersion}
              onReload={onReload}
              reloadLabel={reloadLabel}
            />
          )}
        </KeyValueRow>
        <KeyValueRow label={t('vehicleDetail.identity.vehicleNumber')}>
          <span style={{ fontFamily: 'monospace' }}>{vehicle.vehicleNumber}</span>
        </KeyValueRow>
        <KeyValueRow label={t('vehicleDetail.identity.stammnummer')}>
          <InlineEditField
            value={vehicle.stammnummer ?? ''}
            isEmpty={!vehicle.stammnummer}
            emptyLabel={t('common.notSet')}
            onSave={(v) => onSaveField({ stammnummer: v || null })}
            describeError={describeError}
            isConflict={isStaleVersion}
            onReload={onReload}
            reloadLabel={reloadLabel}
          />
        </KeyValueRow>
        <KeyValueRow label={t('vehicleDetail.identity.typeApprovalNumber')}>
          <InlineEditField
            value={vehicle.typeApprovalNumber ?? ''}
            isEmpty={!vehicle.typeApprovalNumber}
            emptyLabel={t('common.notSet')}
            onSave={(v) => onSaveField({ typeApprovalNumber: v || null })}
            describeError={describeError}
            isConflict={isStaleVersion}
            onReload={onReload}
            reloadLabel={reloadLabel}
          />
        </KeyValueRow>
        <KeyValueRow label={t('vehicleDetail.identity.firstRegistrationDate')}>
          <InlineEditField
            value={vehicle.firstRegistrationDate ? formatDateOnly(vehicle.firstRegistrationDate, locale) : ''}
            editValue={vehicle.firstRegistrationDate ?? ''}
            isEmpty={!vehicle.firstRegistrationDate}
            emptyLabel={t('common.notSet')}
            onSave={(v) => onSaveField({ firstRegistrationDate: v || null })}
            describeError={describeError}
            isConflict={isStaleVersion}
            onReload={onReload}
            reloadLabel={reloadLabel}
          />
        </KeyValueRow>
        <KeyValueRow label={t('vehicleDetail.identity.matchStatus')}>
          <Badge variant="light" color={vehicle.catalogueMatchStatus === 'matched' ? 'grape' : 'gray'}>
            {t(`vehiclesList.matchStatus.${vehicle.catalogueMatchStatus}`)}
          </Badge>
        </KeyValueRow>
      </OverviewCard>

      <OverviewCard title={t('vehicleDetail.parties.title')}>
        <Stack gap="xs">
          {parties.length === 0 && <span style={{ fontStyle: 'italic', color: 'var(--mantine-color-gray-5)' }}>{t('vehicleDetail.parties.none')}</span>}
          {[...currentParties, ...upcomingParties].map((p) => (
            <Group key={p.id} justify="space-between" wrap="nowrap">
              <Group gap={6}>
                <Badge variant="light">{t(`vehicleDetail.parties.role.${p.role}`)}</Badge>
                {isUpcoming(p) && (
                  <KitBadge tone="informational">
                    {t('vehicleDetail.parties.upcoming', { date: formatDate(p.effectiveFrom, locale) })}
                  </KitBadge>
                )}
              </Group>
              <Group gap={4} wrap="nowrap">
                <button
                  type="button"
                  onClick={() => openCustomerOverlay(p.customerId)}
                  style={PARTY_LINK_STYLE}
                >
                  {partyLabel(p)}
                </button>
                {partyMenu(p)}
              </Group>
            </Group>
          ))}
          {endError && (
            <Alert color="red" role="alert">
              {endError}
            </Alert>
          )}

          {!allocating && (
            <Button size="xs" variant="light" leftSection={<UserPlus size={14} />} onClick={() => setAllocating(true)}>
              {t('vehicleDetail.parties.allocate')}
            </Button>
          )}

          {allocating && (
            <Stack gap="xs">
              <Select
                label={t('vehicleDetail.parties.roleLabel')}
                data={ROLES.map((r) => ({ value: r, label: t(`vehicleDetail.parties.role.${r}`) }))}
                value={role}
                onChange={(v) => setRole((v as VehiclePartyRole) ?? 'owner')}
              />
              <Picker
                rows={customerCandidates}
                query={customerSearch}
                onQueryChange={onCustomerSearchChange}
                onSelect={(row) => void allocate(row.id)}
                loading={allocationSaving}
                placeholder={t('vehicleDetail.parties.searchCustomer')}
                emptyLabel={t('vehicleDetail.parties.noCustomers')}
              />
              {allocationError && (
                <Alert color="red" role="alert">
                  {allocationError}
                </Alert>
              )}
              <Button variant="default" size="xs" onClick={closeAllocation}>
                {t('common.cancel')}
              </Button>
            </Stack>
          )}

          {formerParties.length > 0 && (
            <>
              <Button variant="subtle" size="xs" onClick={() => setShowFormer((s) => !s)}>
                {showFormer ? t('vehicleDetail.parties.hideFormer') : t('vehicleDetail.parties.showFormer')}
              </Button>
              {showFormer &&
                formerParties.map((p) => (
                  <Group key={p.id} justify="space-between" style={{ opacity: 0.6 }}>
                    <Group gap={6}>
                      <Badge variant="outline">{t(`vehicleDetail.parties.role.${p.role}`)}</Badge>
                      {isPlannedNeverStarted(p) && <KitBadge tone="slate">{t('vehicleDetail.parties.plannedNeverStarted')}</KitBadge>}
                    </Group>
                    <button
                      type="button"
                      onClick={() => openCustomerOverlay(p.customerId)}
                      style={PARTY_LINK_STYLE}
                    >
                      {partyLabel(p)}
                    </button>
                  </Group>
                ))}
            </>
          )}
        </Stack>
      </OverviewCard>
    </div>
  )
}
