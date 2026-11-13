import { useCallback, useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Badge, Loader } from '@mantine/core'
import { Car } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DetailHeader, DetailTabs, useSetBreadcrumb, type DetailTab } from '@nexotec/ui-kit'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useCanWrite } from '../auth/useCanWrite'
import { holdsNowOrLater } from '../utils/vehicleParty'
import { buildVehicleRowMenu, readVehicleOpenAction, type VehicleOpenAction } from '../components/vehicleRowMenu'
import { IdentityTab } from '../components/vehicle-detail/IdentityTab'
import { PlatesTab } from '../components/vehicle-detail/PlatesTab'
import { isPlateReadLimitError } from '../components/vehicle-detail/plateReadLimit'
import { EndPlateDialog, RecordPlateDialog, type RecordPlateRequest } from '../components/vehicle-detail/PlateDialogs'
import { SpecificationTab } from '../components/vehicle-detail/SpecificationTab'
import { OdometerTab } from '../components/vehicle-detail/OdometerTab'
import { AccessoriesTab } from '../components/vehicle-detail/AccessoriesTab'
import { useIdempotencyKey } from '../hooks/useIdempotencyKey'
import type {
  CustomerPage,
  OdometerSource,
  VehicleAccessoryRead,
  VehicleOdometerReadingRead,
  VehiclePartyAllocationRead,
  VehiclePartyRole,
  VehiclePlateRead,
} from '../api/types'
import type { components } from '../api/schema'
import { apiRefusalText } from '../utils/apiRefusal'
import { stockOpenState } from '../components/stockRowMenu'

// KAN-667 — the Vehicle 360's read of one record: the record and the session's
// own Betrieb's stock car holding it. Derived from the generated schema like
// every type in api/types.ts (move it there once that file is free to change).
type VehicleMdmDetailRead = components['schemas']['VehicleMdmDetailRead']

const DEFAULT_TAB = 'identity'

/**
 * FR-V-16 Vehicle 360 — the same DetailHeader/DetailTabs shell
 * CustomerDetailPage already uses, with the vehicle's own field set and
 * tabs (Identity/Plates/Odometer/Accessories, and C-F's read-only
 * Specification). The header renders from `buildVehicleRowMenu` (ADR-061;
 * FR-V-16 as amended by KAN-468): primary «Allocate to customer», which
 * opens the Identity tab's customer picker; alternative «Request
 * valuation», a new valuation of this car (Sales only). There is no Edit:
 * the identity fields are inline.
 */
export function VehicleDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { t } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const activeTab = searchParams.get('tab') ?? DEFAULT_TAB
  const [customerSearch, setCustomerSearch] = useState('')
  // The Identity tab's customer picker, opened from the header's primary too.
  const [allocating, setAllocating] = useState(false)
  // «Record odometer» / «Record accessory» open their tab with the form focused.
  const [formToFocus, setFormToFocus] = useState<'odometer' | 'accessories' | null>(null)
  const formFocusHandled = useCallback(() => setFormToFocus(null), [])
  // KAN-467 — «Kontrollschild erfassen» and «beenden» (Plates tab, overflow).
  const [recordingPlate, setRecordingPlate] = useState(false)
  const [endingPlate, setEndingPlate] = useState<VehiclePlateRead | null>(null)
  const navigate = useNavigate()
  const { entitlements } = useAuth()
  // KAN-198 / D-A-04: valuations are a Sales-module surface.
  const salesEntitled = entitlements.includes('sales')

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

  const vehicleQuery = useQuery({
    queryKey: ['vehicle-mdm', id],
    queryFn: () => api.get<VehicleMdmDetailRead>(`/vehicle-mdm/${id}`),
    enabled: Boolean(id),
  })
  const platesQuery = useQuery({
    queryKey: ['vehicle-mdm', id, 'plates'],
    queryFn: () => api.get<VehiclePlateRead[]>(`/vehicle-mdm/${id}/plates`),
    // KAN-231: a refusal past the plate-read limit is final for now and is
    // audited server-side on every attempt — never retry it.
    retry: (failureCount, error) => !isPlateReadLimitError(error) && failureCount < 3,
    // Every fetch is an audited plate read; a window refocus is not a new look.
    refetchOnWindowFocus: false,
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

  // KAN-63 — the breadcrumb must show the same human-readable label the
  // header's own <DetailHeader title> already renders (vehicleNumber),
  // never the raw route param — that used to leak the UUIDv7 primary key
  // into the most prominent chrome on the page.
  useSetBreadcrumb([
    t('shell.nav.masterData'),
    { label: t('shell.nav.vehicles'), href: '/vehicles' },
    vehicle?.vehicleNumber ?? t('vehicleDetail.header.vehicleFallback'),
  ])

  // KAN-266: one Idempotency-Key per submission of each create — a retry
  // after a failed save reuses it, so the server replays instead of writing
  // twice; a different request (another value, another vehicle: this screen
  // stays mounted when :id changes) gets a new one. Every successful write
  // of a kind renews that kind's key, so a key left by a create whose
  // response was lost never replays, creating nothing, for a later identical
  // value.
  const allocateKey = useIdempotencyKey()
  const odometerKey = useIdempotencyKey()
  const accessoryKey = useIdempotencyKey()
  const plateKey = useIdempotencyKey()

  const invalidatePartyRoles = () => {
    allocateKey.renew()
    void queryClient.invalidateQueries({ queryKey: ['vehicle-mdm', id, 'party-roles'] })
  }

  const saveField = async (patch: Record<string, unknown>) => {
    if (!vehicle || !id) return
    const updated = await api.patch<VehicleMdmDetailRead>(`/vehicle-mdm/${id}`, patch, { 'If-Match': String(vehicle.version) })
    queryClient.setQueryData(['vehicle-mdm', id], updated)
  }

  const reload = () => void vehicleQuery.refetch()

  const allocate = async (customerId: string, role: VehiclePartyRole) => {
    const path = `/vehicle-mdm/${id}/allocate`
    const body = { customerId, role }
    await api.post(path, body, allocateKey.headers([path, body]))
    invalidatePartyRoles()
  }

  // KAN-583 (ruling 2A): «Eintrag beenden» — the customer's Disconnect,
  // which ends a current entry now and cancels a planned one.
  const canEndPartyEntry = useCanWrite('customer_vehicle_links')
  const endPartyEntry = async (party: VehiclePartyAllocationRead) => {
    await api.delete(`/customers/${party.customerId}/vehicles/${party.id}`)
    invalidatePartyRoles()
  }

  const addOdometerReading = async (value: number, readingDate: string, source: OdometerSource) => {
    const path = `/vehicle-mdm/${id}/odometer-readings`
    const body = { value, readingDate, source }
    await api.post(path, body, odometerKey.headers([path, body]))
    odometerKey.renew()
    void queryClient.invalidateQueries({ queryKey: ['vehicle-mdm', id, 'odometer-readings'] })
  }

  const addAccessory = async (accessoryType: string, description: string, validFrom: string) => {
    const path = `/vehicle-mdm/${id}/accessories`
    const body = { accessoryType, description: description || null, validFrom }
    await api.post(path, body, accessoryKey.headers([path, body]))
    accessoryKey.renew()
    void queryClient.invalidateQueries({ queryKey: ['vehicle-mdm', id, 'accessories'] })
  }

  const invalidatePlates = () => void queryClient.invalidateQueries({ queryKey: ['vehicle-mdm', id, 'plates'] })

  const recordPlate = async (request: RecordPlateRequest) => {
    const path = `/vehicle-mdm/${id}/plates`
    await api.post(path, request, plateKey.headers([path, request]))
    plateKey.renew()
    invalidatePlates()
  }

  const endPlate = async (plate: VehiclePlateRead, validTo: string) => {
    await api.post(`/vehicle-mdm/${id}/plates/${plate.id}/end`, { validTo }, { 'If-Match': String(plate.version) })
    invalidatePlates()
  }

  const removeAccessory = async (accessoryId: string) => {
    await api.delete(`/vehicle-mdm/${id}/accessories/${accessoryId}`)
    accessoryKey.renew()
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
      { id: 'specification', label: t('vehicleDetail.tabs.specification') },
    ],
    [t, platesQuery.data, odometerQuery.data, accessoriesQuery.data],
  )

  const startAllocating = () => {
    setActiveTab('identity')
    setAllocating(true)
  }
  const startRecordingOdometer = () => {
    setActiveTab('odometer')
    setFormToFocus('odometer')
  }
  const startRecordingAccessory = () => {
    setActiveTab('accessories')
    setFormToFocus('accessories')
  }
  // The dialog opens once the Plates tab shows: opened in the same render as
  // the tab change, the router's navigation to the tab was lost (the list's
  // row menu → «Kontrollschild erfassen» landed on Identity with the dialog).
  const [plateDialogRequested, setPlateDialogRequested] = useState(false)
  const startRecordingPlate = () => {
    plateKey.renew()
    if (activeTab === 'plates') setRecordingPlate(true)
    else {
      setActiveTab('plates')
      setPlateDialogRequested(true)
    }
  }
  useEffect(() => {
    if (!plateDialogRequested || activeTab !== 'plates') return
    setPlateDialogRequested(false)
    setRecordingPlate(true)
  }, [plateDialogRequested, activeTab])
  const startAction: Record<VehicleOpenAction, () => void> = {
    allocate: startAllocating,
    recordOdometer: startRecordingOdometer,
    recordAccessory: startRecordingAccessory,
    recordPlate: startRecordingPlate,
  }

  // KAN-503 — the Vehicles list's row menu opens the car at the action
  // picked there (router state, never the URL). It runs once the vehicle has
  // loaded, exactly as the header's would. The tab change it makes is a
  // navigation without state, which drops the action: a reload, Back or
  // Forward never runs it again.
  const location = useLocation()
  const openAction = readVehicleOpenAction(location.state)
  const vehicleLoaded = Boolean(vehicleQuery.data)
  useEffect(() => {
    if (openAction && vehicleLoaded) startAction[openAction]()
    // startAction is recreated on every render; the action runs once per
    // navigation that carries it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openAction, vehicleLoaded])

  const rowMenu = buildVehicleRowMenu(t, {
    onAllocate: startAllocating,
    onRecordOdometer: startRecordingOdometer,
    onRecordAccessory: startRecordingAccessory,
    onRecordPlate: startRecordingPlate,
    onCopyVin: () => void navigator.clipboard.writeText(vehicle?.vin ?? ''),
    onRequestValuation: salesEntitled && id ? () => navigate(`/valuations/new?vehicle=${id}`) : undefined,
  })

  if (vehicleQuery.isLoading) return <Loader />
  if (vehicleQuery.isError || !vehicle) {
    return (
      <Alert color="red" title={t('vehicleDetail.header.failedToLoad')}>
        {apiRefusalText(t, vehicleQuery.error, 'vehicleDetail.errors.somethingWentWrong')}
      </Alert>
    )
  }

  const heldInStock = vehicle.stockItem

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <DetailHeader
        labels={{ moreActions: t('common.moreActions') }}
        entityMark={<Car size={24} />}
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
        primaryAction={rowMenu.primary}
        alternativeAction={rowMenu.alternative}
        overflowActions={rowMenu.overflow}
      />

      <DetailTabs tabs={tabs} activeTab={activeTab} onTabChange={setActiveTab} />

      {activeTab === 'identity' && (
        <IdentityTab
          vehicle={vehicle}
          parties={partiesQuery.data ?? []}
          // KAN-644: a row ending later (a current holder until a planned handover)
          // is not former; a cancelled planned entry is (KAN-583).
          formerParties={(formerPartiesQuery.data ?? []).filter((p) => !holdsNowOrLater(p))}
          onSaveField={saveField}
          onReload={reload}
          onAllocate={allocate}
          canEndEntry={canEndPartyEntry}
          onEndEntry={endPartyEntry}
          stockItem={vehicle.stockItem}
          // KAN-667: the stock car opens at its «VIN korrigieren» (KAN-557), as
          // the Stock list's row menu opens it (router state, never the URL).
          onCorrectVinInStock={
            salesEntitled && heldInStock
              ? () => navigate(`/stock/${heldInStock.id}`, { state: stockOpenState('correctVin') })
              : undefined
          }
          allocating={allocating}
          onAllocatingChange={setAllocating}
          customerCandidates={customerCandidates}
          customerSearch={customerSearch}
          onCustomerSearchChange={setCustomerSearch}
          onCustomerOverlayClose={invalidatePartyRoles}
        />
      )}
      {activeTab === 'plates' && <PlatesTab
          plates={platesQuery.data ?? []}
          loading={platesQuery.isLoading}
          limitReached={isPlateReadLimitError(platesQuery.error)}
          onRecord={startRecordingPlate}
          onEnd={setEndingPlate}
        />}
      <RecordPlateDialog opened={recordingPlate} onClose={() => setRecordingPlate(false)} onRecord={recordPlate} />
      <EndPlateDialog plate={endingPlate} onClose={() => setEndingPlate(null)} onEnd={endPlate} />
      {activeTab === 'odometer' && (
        <OdometerTab
          readings={odometerQuery.data ?? []}
          loading={odometerQuery.isLoading}
          onAdd={addOdometerReading}
          focusRequested={formToFocus === 'odometer'}
          onFocusHandled={formFocusHandled}
        />
      )}
      {activeTab === 'accessories' && (
        <AccessoriesTab
          accessories={accessoriesQuery.data ?? []}
          loading={accessoriesQuery.isLoading}
          onAdd={addAccessory}
          onRemove={removeAccessory}
          focusRequested={formToFocus === 'accessories'}
          onFocusHandled={formFocusHandled}
        />
      )}
      {activeTab === 'specification' && <SpecificationTab vehicleId={vehicle.id} />}
    </div>
  )
}
