import { useState } from 'react'
import { Badge, Button, Group, Select, Stack } from '@mantine/core'
import { UserPlus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { InlineEditField, KeyValueRow, OverviewCard, Picker, purple, useOverlay } from '@nexotec/ui-kit'
import { ApiError } from '../../api/client'
import { CustomerDetailContent } from '../../pages/CustomerDetailPage'
import type { VehicleMdmRead, VehiclePartyAllocationRead, VehiclePartyRole } from '../../api/types'

interface IdentityTabProps {
  vehicle: VehicleMdmRead
  parties: VehiclePartyAllocationRead[]
  formerParties: VehiclePartyAllocationRead[]
  onSaveField: (patch: Record<string, unknown>) => Promise<void>
  onReload: () => void
  onAllocate: (customerId: string, role: VehiclePartyRole) => Promise<void>
  customerCandidates: { id: string; label: string; sublabel?: string }[]
  customerSearch: string
  onCustomerSearchChange: (q: string) => void
}

const ROLES: VehiclePartyRole[] = ['owner', 'keeper', 'driver']

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
  customerCandidates,
  customerSearch,
  onCustomerSearchChange,
}: IdentityTabProps) {
  const { t } = useTranslation()
  const [allocating, setAllocating] = useState(false)
  const [role, setRole] = useState<VehiclePartyRole>('owner')
  const [showFormer, setShowFormer] = useState(false)
  const isConflict = (err: unknown) => err instanceof ApiError && err.status === 409
  const overlay = useOverlay()

  // § ADR-059 — "opening a record from inside a process renders it as an
  // overlay on top, not a navigation." Working on a vehicle's party
  // allocation and needing to check the customer behind a raw id is
  // exactly that process — this used to just print the id as plain text.
  // No onClose invalidation: nothing this tab renders (role, customer id)
  // can change from inside the overlaid Customer 360, so there is
  // genuinely nothing here for U-11 to invalidate on close.
  const openCustomerOverlay = (customerId: string) => {
    overlay.push({
      key: `customer-overlay-${customerId}`,
      content: <CustomerDetailContent customerId={customerId} embedded />,
    })
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
      <OverviewCard title={t('vehicleDetail.identity.title')}>
        <KeyValueRow label={t('vehicleDetail.identity.vin')}>
          <InlineEditField
            value={vehicle.vin}
            onSave={(v) => onSaveField({ vin: v.toUpperCase() })}
            isConflict={isConflict}
            onReload={onReload}
          />
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
            isConflict={isConflict}
            onReload={onReload}
          />
        </KeyValueRow>
        <KeyValueRow label={t('vehicleDetail.identity.typeApprovalNumber')}>
          <InlineEditField
            value={vehicle.typeApprovalNumber ?? ''}
            isEmpty={!vehicle.typeApprovalNumber}
            emptyLabel={t('common.notSet')}
            onSave={(v) => onSaveField({ typeApprovalNumber: v || null })}
            isConflict={isConflict}
            onReload={onReload}
          />
        </KeyValueRow>
        <KeyValueRow label={t('vehicleDetail.identity.firstRegistrationDate')}>
          <InlineEditField
            value={vehicle.firstRegistrationDate ?? ''}
            isEmpty={!vehicle.firstRegistrationDate}
            emptyLabel={t('common.notSet')}
            onSave={(v) => onSaveField({ firstRegistrationDate: v || null })}
            isConflict={isConflict}
            onReload={onReload}
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
          {parties.map((p) => (
            <Group key={p.id} justify="space-between">
              <Badge variant="light">{t(`vehicleDetail.parties.role.${p.role}`)}</Badge>
              <button
                type="button"
                onClick={() => openCustomerOverlay(p.customerId)}
                style={{ fontFamily: 'monospace', fontSize: 12, color: purple[6], background: 'none', border: 'none', cursor: 'pointer', padding: 0, textDecoration: 'underline' }}
              >
                {p.customerId}
              </button>
            </Group>
          ))}

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
                onSelect={(row) => {
                  void onAllocate(row.id, role).then(() => setAllocating(false))
                }}
                placeholder={t('vehicleDetail.parties.searchCustomer')}
                emptyLabel={t('vehicleDetail.parties.noCustomers')}
              />
              <Button variant="default" size="xs" onClick={() => setAllocating(false)}>
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
                    <Badge variant="outline">{t(`vehicleDetail.parties.role.${p.role}`)}</Badge>
                    <button
                      type="button"
                      onClick={() => openCustomerOverlay(p.customerId)}
                      style={{ fontFamily: 'monospace', fontSize: 12, color: purple[6], background: 'none', border: 'none', cursor: 'pointer', padding: 0, textDecoration: 'underline' }}
                    >
                      {p.customerId}
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
