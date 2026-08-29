import { useMemo, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Badge, Loader } from '@mantine/core'
import { Car, Copy, GitMerge } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DetailHeader, DetailTabs, useSetBreadcrumb, type DetailTab } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import { IdentityTab } from '../components/vehicle-detail/IdentityTab'
import { PlatesTab } from '../components/vehicle-detail/PlatesTab'
import { OdometerTab } from '../components/vehicle-detail/OdometerTab'
import { AccessoriesTab } from '../components/vehicle-detail/AccessoriesTab'
import type {
  CustomerPage,
  OdometerSource,
  VehicleAccessoryRead,
  VehicleMdmRead,
  VehicleOdometerReadingRead,
  VehiclePartyAllocationRead,
  VehiclePartyRole,
  VehiclePlateRead,
} from '../api/types'

const DEFAULT_TAB = 'identity'

/**
 * FR-V-16 Vehicle 360 — the same DetailHeader/DetailTabs shell
 * CustomerDetailPage already uses, with the vehicle's own field set and
 * tabs (Identity/Plates/Odometer/Accessories). Primary action Edit
 * (identity fields are already inline, so this focuses the Identity
 * tab); alternative is Allocate to customer (ADR-061's action contract).
 */
export function VehicleDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { t } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const activeTab = searchParams.get('tab') ?? DEFAULT_TAB
  const [customerSearch, setCustomerSearch] = useState('')

  const setActiveTab = (tab: string) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (tab === DEFAULT_TAB) next.delete('tab')
        else next.set('tab', tab)
        return next
      },
      { replace: true },
    )
  }

  useSetBreadcrumb([t('shell.nav.masterData'), t('shell.nav.vehicles'), id ?? ''])

  const vehicleQuery = useQuery({
    queryKey: ['vehicle-mdm', id],
    queryFn: () => api.get<VehicleMdmRead>(`/vehicle-mdm/${id}`),
    enabled: Boolean(id),
  })
  const platesQuery = useQuery({
    queryKey: ['vehicle-mdm', id, 'plates'],
    queryFn: () => api.get<VehiclePlateRead[]>(`/vehicle-mdm/${id}/plates`),
    enabled: Boolean(id),
  })
  const odometerQuery = useQuery({
    queryKey: ['vehicle-mdm', id, 'odometer-readings'],
    queryFn: () => api.get<VehicleOdometerReadingRead[]>(`/vehicle-mdm/${id}/odometer-readings`),
    enabled: Boolean(id),
  })
  const accessoriesQuery = useQuery({
    queryKey: ['vehicle-mdm', id, 'accessories'],
    queryFn: () => api.get<VehicleAccessoryRead[]>(`/vehicle-mdm/${id}/accessories`),
    enabled: Boolean(id),
  })
  const partiesQuery = useQuery({
    queryKey: ['vehicle-mdm', id, 'party-roles'],
    queryFn: () => api.get<VehiclePartyAllocationRead[]>(`/vehicle-mdm/${id}/party-roles`),
    enabled: Boolean(id),
  })
  const formerPartiesQuery = useQuery({
    queryKey: ['vehicle-mdm', id, 'party-roles', 'closed'],
    queryFn: () => api.get<VehiclePartyAllocationRead[]>(`/vehicle-mdm/${id}/party-roles?include_closed=true`),
    enabled: Boolean(id),
  })
  const customersQuery = useQuery({
    queryKey: ['customers', 'picker', customerSearch],
    queryFn: () => api.get<CustomerPage>(`/customers?q=${encodeURIComponent(customerSearch)}&limit=10`),
    enabled: customerSearch.length > 0,
  })

  const vehicle = vehicleQuery.data

  const invalidatePartyRoles = () => {
    void queryClient.invalidateQueries({ queryKey: ['vehicle-mdm', id, 'party-roles'] })
  }

  const saveField = async (patch: Record<string, unknown>) => {
    if (!vehicle || !id) return
    const updated = await api.patch<VehicleMdmRead>(`/vehicle-mdm/${id}`, patch, { 'If-Match': String(vehicle.version) })
    queryClient.setQueryData(['vehicle-mdm', id], updated)
  }

  const reload = () => void vehicleQuery.refetch()

  const allocate = async (customerId: string, role: VehiclePartyRole) => {
    await api.post(`/vehicle-mdm/${id}/allocate`, { customerId, role })
    invalidatePartyRoles()
  }

  const addOdometerReading = async (value: number, readingDate: string, source: OdometerSource) => {
    await api.post(`/vehicle-mdm/${id}/odometer-readings`, { value, readingDate, source })
    void queryClient.invalidateQueries({ queryKey: ['vehicle-mdm', id, 'odometer-readings'] })
  }

  const addAccessory = async (accessoryType: string, description: string, validFrom: string) => {
    await api.post(`/vehicle-mdm/${id}/accessories`, { accessoryType, description: description || null, validFrom })
    void queryClient.invalidateQueries({ queryKey: ['vehicle-mdm', id, 'accessories'] })
  }

  const removeAccessory = async (accessoryId: string) => {
    await api.delete(`/vehicle-mdm/${id}/accessories/${accessoryId}`)
    void queryClient.invalidateQueries({ queryKey: ['vehicle-mdm', id, 'accessories'] })
  }

  const customerCandidates = useMemo(
    () =>
      (customersQuery.data?.items ?? []).map((c) => ({
        id: c.id,
        label: c.customerType === 'business' ? (c.companyName ?? '') : `${c.firstName ?? ''} ${c.lastName ?? ''}`.trim(),
        sublabel: c.customerNumber,
      })),
    [customersQuery.data],
  )

  const tabs: DetailTab[] = useMemo(
    () => [
      { id: 'identity', label: t('vehicleDetail.tabs.identity') },
      { id: 'plates', label: t('vehicleDetail.tabs.plates'), count: platesQuery.data?.length },
      { id: 'odometer', label: t('vehicleDetail.tabs.odometer'), count: odometerQuery.data?.length },
      { id: 'accessories', label: t('vehicleDetail.tabs.accessories'), count: accessoriesQuery.data?.length },
    ],
    [t, platesQuery.data, odometerQuery.data, accessoriesQuery.data],
  )

  if (vehicleQuery.isLoading) return <Loader />
  if (vehicleQuery.isError || !vehicle) {
    return (
      <Alert color="red" title={t('vehicleDetail.header.failedToLoad')}>
        {vehicleQuery.error instanceof ApiError ? vehicleQuery.error.message : t('vehicleDetail.errors.somethingWentWrong')}
      </Alert>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <DetailHeader
        entityMark={<Car size={22} />}
        title={vehicle.vehicleNumber}
        businessKey={vehicle.vin}
        badges={
          <>
            <Badge variant="light">{t(`vehiclesList.status.${vehicle.vehicleStatus}`)}</Badge>
            <Badge variant="light" color={vehicle.catalogueMatchStatus === 'matched' ? 'grape' : 'gray'}>
              {t(`vehiclesList.matchStatus.${vehicle.catalogueMatchStatus}`)}
            </Badge>
          </>
        }
        overflowActions={{
          exportPrint: [
            {
              label: t('vehicleDetail.header.copyVin'),
              icon: <Copy size={16} />,
              onClick: () => navigator.clipboard.writeText(vehicle.vin),
            },
          ],
          destructive: [
            {
              label: t('vehicleDetail.header.merge'),
              icon: <GitMerge size={16} />,
              disabled: true,
              // § ADR-061: "disabled items shown-and-explained, never
              // hidden" — this had no reason at all before RowMenu grew
              // `disabledReason` support.
              disabledReason: t('vehicleDetail.header.mergeDisabledReason'),
              onClick: () => {},
            },
          ],
        }}
      />

      <DetailTabs tabs={tabs} activeTab={activeTab} onTabChange={setActiveTab} />

      {activeTab === 'identity' && (
        <IdentityTab
          vehicle={vehicle}
          parties={partiesQuery.data ?? []}
          formerParties={(formerPartiesQuery.data ?? []).filter((p) => p.effectiveTo !== null)}
          onSaveField={saveField}
          onReload={reload}
          onAllocate={allocate}
          customerCandidates={customerCandidates}
          customerSearch={customerSearch}
          onCustomerSearchChange={setCustomerSearch}
        />
      )}
      {activeTab === 'plates' && <PlatesTab plates={platesQuery.data ?? []} loading={platesQuery.isLoading} />}
      {activeTab === 'odometer' && (
        <OdometerTab readings={odometerQuery.data ?? []} loading={odometerQuery.isLoading} onAdd={addOdometerReading} />
      )}
      {activeTab === 'accessories' && (
        <AccessoriesTab
          accessories={accessoriesQuery.data ?? []}
          loading={accessoriesQuery.isLoading}
          onAdd={addAccessory}
          onRemove={removeAccessory}
        />
      )}
    </div>
  )
}
