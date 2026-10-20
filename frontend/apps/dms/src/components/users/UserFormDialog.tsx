import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  Checkbox,
  FileInput,
  Group,
  Image,
  MultiSelect,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
} from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { FormDialog } from '@nexotec/ui-kit'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import type {
  AccessRole,
  LocationRead,
  StandardTextCategory,
  TextDelegationsRead,
  UserCreate,
  UserAdminRead,
  UserRole,
  UserUpdate,
} from '../../api/types'
import { useIdempotencyKey } from '../../hooks/useIdempotencyKey'
import {
  GRANTABLE_ACCESS_ROLES,
  TEXT_CATEGORY_VALUES,
  USER_ROLE_VALUES,
  accessRoleLabel,
  textCategoryLabel,
  userDisplayName,
  userRoleLabel,
} from '../../userOptions'

export interface UserFormDialogProps {
  opened: boolean
  onClose: () => void
  dealershipId: string
  /** `null` invites a new person; a user edits that person. */
  user: UserAdminRead | null
  locations: LocationRead[]
  /** The Betrieb's people, for the line-manager picker. */
  colleagues: UserAdminRead[]
  /** The signed-in user: their own manager flag cannot be edited here. */
  selfId: string
  onSaved: (user: UserAdminRead) => void
}

interface FormValues {
  firstName: string
  lastName: string
  email: string
  phone: string
  directDial: string
  role: UserRole
  primaryAccessRole: AccessRole
  additionalAccessRoles: AccessRole[]
  isDealerManager: boolean
  department: string
  employeeNumber: string
  lineManagerUserId: string | null
  primaryLocationId: string | null
  additionalLocationIds: string[]
  salesAdvisorCode: string
  commissionRelevant: boolean
  validFrom: string
  validTo: string
  delegations: StandardTextCategory[]
}

const EMPTY: FormValues = {
  firstName: '',
  lastName: '',
  email: '',
  phone: '',
  directDial: '',
  role: 'sales',
  primaryAccessRole: 'sales',
  additionalAccessRoles: [],
  isDealerManager: false,
  department: '',
  employeeNumber: '',
  lineManagerUserId: null,
  primaryLocationId: null,
  additionalLocationIds: [],
  salesAdvisorCode: '',
  commissionRelevant: false,
  validFrom: '',
  validTo: '',
  delegations: [],
}

function fromUser(user: UserAdminRead): FormValues {
  const [primary, ...rest] = user.accessRoles.filter((r) => r !== 'platform_admin')
  return {
    firstName: user.firstName,
    lastName: user.lastName,
    email: user.email,
    phone: user.phone ?? '',
    directDial: user.directDial ?? '',
    role: user.role,
    primaryAccessRole: primary ?? 'sales',
    additionalAccessRoles: rest,
    isDealerManager: user.isDealerManager,
    department: user.department ?? '',
    employeeNumber: user.employeeNumber ?? '',
    lineManagerUserId: user.lineManagerUserId ?? null,
    primaryLocationId: user.primaryLocationId ?? null,
    additionalLocationIds: user.additionalLocationIds ?? [],
    salesAdvisorCode: user.salesAdvisorCode ?? '',
    commissionRelevant: user.commissionRelevant ?? false,
    validFrom: user.validFrom ?? '',
    validTo: user.validTo ?? '',
    delegations: [],
  }
}

const orNull = (value: string) => (value.trim() === '' ? null : value.trim())
const FORM_ID = 'user-form'

/**
 * KAN-200 — the one user form, for inviting and for editing (UI/UX
 * Specification § Forms: "the create form IS the edit form"; the ticket's
 * "edit drawer" is this dialog, the spec winning on presentation). Sections
 * follow the PRD's field table: identity · organisation · access ·
 * validity · commercial · signature. A manager's own flag is not editable
 * here (the last-manager rule would refuse it anyway, FR-A-13); the API
 * says so for a colleague who is the last one.
 */
export function UserFormDialog({ opened, onClose, dealershipId, user, locations, colleagues, selfId, onSaved }: UserFormDialogProps) {
  const { t } = useTranslation()
  const [form, setForm] = useState<FormValues>(EMPTY)
  const [signatureFile, setSignatureFile] = useState<File | null>(null)
  const [removeSignature, setRemoveSignature] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const idempotency = useIdempotencyKey()
  const editing = user !== null

  const delegations = useQuery({
    queryKey: ['users', dealershipId, user?.id, 'text-delegations'],
    queryFn: () => api.get<TextDelegationsRead>(`/dealerships/${dealershipId}/users/${user!.id}/text-delegations`),
    enabled: opened && editing,
  })

  useEffect(() => {
    if (!opened) return
    setForm(user ? fromUser(user) : EMPTY)
    setSignatureFile(null)
    setRemoveSignature(false)
    setError(null)
    idempotency.renew()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, user?.id])

  useEffect(() => {
    if (delegations.data) setForm((f) => ({ ...f, delegations: delegations.data.categories }))
  }, [delegations.data])

  const signatureUrl = useQuery({
    queryKey: ['users', dealershipId, user?.id, 'signature', user?.version],
    queryFn: () => api.getBlobUrl(`/dealerships/${dealershipId}/users/${user!.id}/signature`),
    enabled: opened && editing && Boolean(user?.hasSignatureImage),
  })

  const set = <K extends keyof FormValues>(key: K, value: FormValues[K]) => setForm((f) => ({ ...f, [key]: value }))

  const locationOptions = useMemo(() => locations.map((l) => ({ value: l.id, label: l.name })), [locations])
  const colleagueOptions = useMemo(
    () => colleagues.filter((c) => c.id !== user?.id && c.status !== 'deactivated').map((c) => ({ value: c.id, label: userDisplayName(c) })),
    [colleagues, user?.id],
  )
  const additionalRoleOptions = GRANTABLE_ACCESS_ROLES.filter((r) => r !== form.primaryAccessRole)

  const say = (err: unknown) => {
    if (err instanceof ApiError && err.status === 409) setError(t('usersAdmin.form.conflict'))
    else if (err instanceof ApiError) setError(err.message)
    else setError(t('common.requestFailed'))
  }

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    const accessRoles: AccessRole[] = [form.primaryAccessRole, ...form.additionalAccessRoles.filter((r) => r !== form.primaryAccessRole)]
    try {
      let saved: UserAdminRead
      if (!editing) {
        const body: UserCreate = {
          firstName: form.firstName.trim(),
          lastName: form.lastName.trim(),
          email: form.email.trim(),
          phone: orNull(form.phone),
          directDial: orNull(form.directDial),
          role: form.role,
          accessRoles,
          isDealerManager: form.isDealerManager,
          department: orNull(form.department),
          employeeNumber: orNull(form.employeeNumber),
          lineManagerUserId: form.lineManagerUserId,
          primaryLocationId: form.primaryLocationId,
          additionalLocationIds: form.additionalLocationIds,
          salesAdvisorCode: orNull(form.salesAdvisorCode),
          commissionRelevant: form.commissionRelevant,
          validFrom: orNull(form.validFrom),
          validTo: orNull(form.validTo),
        }
        saved = await api.post<UserAdminRead>(`/dealerships/${dealershipId}/users`, body, idempotency.headers(body))
      } else {
        const before = fromUser(user)
        const patch: UserUpdate = {}
        if (form.firstName.trim() !== before.firstName) patch.firstName = form.firstName.trim()
        if (form.lastName.trim() !== before.lastName) patch.lastName = form.lastName.trim()
        if (form.email.trim() !== before.email) patch.email = form.email.trim()
        if (orNull(form.phone) !== orNull(before.phone)) patch.phone = orNull(form.phone)
        if (orNull(form.directDial) !== orNull(before.directDial)) patch.directDial = orNull(form.directDial)
        if (form.role !== before.role) patch.role = form.role
        const beforeRoles = [before.primaryAccessRole, ...before.additionalAccessRoles]
        if (accessRoles.join(',') !== beforeRoles.join(',')) patch.accessRoles = accessRoles
        if (form.isDealerManager !== before.isDealerManager) patch.isDealerManager = form.isDealerManager
        if (orNull(form.department) !== orNull(before.department)) patch.department = orNull(form.department)
        if (orNull(form.employeeNumber) !== orNull(before.employeeNumber)) patch.employeeNumber = orNull(form.employeeNumber)
        if (form.lineManagerUserId !== before.lineManagerUserId) patch.lineManagerUserId = form.lineManagerUserId
        if (form.primaryLocationId !== before.primaryLocationId) patch.primaryLocationId = form.primaryLocationId
        if (form.additionalLocationIds.join(',') !== before.additionalLocationIds.join(','))
          patch.additionalLocationIds = form.additionalLocationIds
        if (orNull(form.salesAdvisorCode) !== orNull(before.salesAdvisorCode)) patch.salesAdvisorCode = orNull(form.salesAdvisorCode)
        if (form.commissionRelevant !== before.commissionRelevant) patch.commissionRelevant = form.commissionRelevant
        if (orNull(form.validFrom) !== orNull(before.validFrom)) patch.validFrom = orNull(form.validFrom)
        if (orNull(form.validTo) !== orNull(before.validTo)) patch.validTo = orNull(form.validTo)
        saved =
          Object.keys(patch).length > 0
            ? await api.patch<UserAdminRead>(`/dealerships/${dealershipId}/users/${user.id}`, patch, { 'If-Match': String(user.version) })
            : user
      }
      // Delegations are a child collection with their own full-replacement PUT (KAN-199).
      const currentDelegations = delegations.data?.categories ?? []
      if (editing ? form.delegations.join(',') !== [...currentDelegations].join(',') : form.delegations.length > 0) {
        const result = await api.put<TextDelegationsRead>(
          `/dealerships/${dealershipId}/users/${saved.id}/text-delegations`,
          { categories: form.delegations },
          { 'If-Match': String(saved.version) },
        )
        saved = { ...saved, version: result.userVersion }
      }
      if (signatureFile) {
        saved = await api.putBinary<UserAdminRead>(
          `/dealerships/${dealershipId}/users/${saved.id}/signature`,
          signatureFile,
          signatureFile.type,
          { 'If-Match': String(saved.version) },
        )
      } else if (removeSignature && saved.hasSignatureImage) {
        saved = await api.delete<UserAdminRead>(`/dealerships/${dealershipId}/users/${saved.id}/signature`, { 'If-Match': String(saved.version) })
      }
      idempotency.renew()
      onSaved(saved)
    } catch (err) {
      say(err)
    } finally {
      setSubmitting(false)
    }
  }

  // The kit's one form dialog (ui.md: components come from the kit; UI/UX
  // Specification § Forms): its footer submits the form below, so Enter in
  // a field and the button do the same thing.
  return (
    <FormDialog
      opened={opened}
      onClose={onClose}
      title={editing ? t('usersAdmin.form.editTitle', { name: userDisplayName(user) }) : t('usersAdmin.form.inviteTitle')}
      size="lg"
      formId={FORM_ID}
      onSubmit={submit}
      submitLabel={editing ? t('common.save') : t('usersAdmin.form.invite')}
      cancelLabel={t('common.cancel')}
      submitting={submitting}
    >
      <form
        id={FORM_ID}
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
        noValidate
      >
        <Stack gap="md">
          {error && (
            <Alert color="red" role="alert">
              {error}
            </Alert>
          )}

          <Text fw={600} size="sm">
            {t('usersAdmin.form.sections.identity')}
          </Text>
          <SimpleGrid cols={2}>
            <TextInput label={t('usersAdmin.fields.firstName')} value={form.firstName} onChange={(e) => set('firstName', e.currentTarget.value)} required />
            <TextInput label={t('usersAdmin.fields.lastName')} value={form.lastName} onChange={(e) => set('lastName', e.currentTarget.value)} required />
            <TextInput label={t('usersAdmin.fields.email')} type="email" value={form.email} onChange={(e) => set('email', e.currentTarget.value)} required disabled={editing && user.status !== 'invited'} description={editing && user.status !== 'invited' ? t('usersAdmin.fields.emailLocked') : undefined} />
            <TextInput label={t('usersAdmin.fields.mobile')} value={form.phone} onChange={(e) => set('phone', e.currentTarget.value)} placeholder="+41 79 …" />
            <TextInput label={t('usersAdmin.fields.directDial')} value={form.directDial} onChange={(e) => set('directDial', e.currentTarget.value)} placeholder="+41 44 …" />
            <TextInput label={t('usersAdmin.fields.employeeNumber')} value={form.employeeNumber} onChange={(e) => set('employeeNumber', e.currentTarget.value)} />
          </SimpleGrid>

          <Text fw={600} size="sm">
            {t('usersAdmin.form.sections.organisation')}
          </Text>
          <SimpleGrid cols={2}>
            <Select
              label={t('usersAdmin.fields.jobTitle')}
              data={USER_ROLE_VALUES.map((r) => ({ value: r, label: userRoleLabel(t, r) }))}
              value={form.role}
              onChange={(value) => value && set('role', value as UserRole)}
              allowDeselect={false}
            />
            <TextInput label={t('usersAdmin.fields.department')} value={form.department} onChange={(e) => set('department', e.currentTarget.value)} />
            <Select
              label={t('usersAdmin.fields.lineManager')}
              data={colleagueOptions}
              value={form.lineManagerUserId}
              onChange={(value) => set('lineManagerUserId', value)}
              searchable
              clearable
              placeholder={t('usersAdmin.fields.none')}
            />
            <Select
              label={t('usersAdmin.fields.primaryLocation')}
              data={locationOptions}
              value={form.primaryLocationId}
              onChange={(value) => set('primaryLocationId', value)}
              clearable
              placeholder={locationOptions.length === 0 ? t('usersAdmin.fields.noLocationsYet') : t('usersAdmin.fields.none')}
              disabled={locationOptions.length === 0}
            />
            <MultiSelect
              label={t('usersAdmin.fields.additionalLocations')}
              data={locationOptions.filter((o) => o.value !== form.primaryLocationId)}
              value={form.additionalLocationIds}
              onChange={(value) => set('additionalLocationIds', value)}
              disabled={locationOptions.length === 0}
              placeholder={locationOptions.length === 0 ? t('usersAdmin.fields.noLocationsYet') : undefined}
            />
          </SimpleGrid>

          <Text fw={600} size="sm">
            {t('usersAdmin.form.sections.access')}
          </Text>
          <SimpleGrid cols={2}>
            <Select
              label={t('usersAdmin.fields.primaryRole')}
              description={t('usersAdmin.fields.primaryRoleHint')}
              data={GRANTABLE_ACCESS_ROLES.map((r) => ({ value: r, label: accessRoleLabel(t, r) }))}
              value={form.primaryAccessRole}
              onChange={(value) =>
                value &&
                setForm((f) => ({
                  ...f,
                  primaryAccessRole: value as AccessRole,
                  additionalAccessRoles: f.additionalAccessRoles.filter((r) => r !== value),
                }))
              }
              allowDeselect={false}
            />
            <Checkbox.Group
              label={t('usersAdmin.fields.alsoHasAccessTo')}
              value={form.additionalAccessRoles}
              onChange={(value) => set('additionalAccessRoles', value as AccessRole[])}
            >
              <Stack gap={4} mt={4}>
                {additionalRoleOptions.map((r) => (
                  <Checkbox key={r} value={r} label={accessRoleLabel(t, r)} />
                ))}
              </Stack>
            </Checkbox.Group>
          </SimpleGrid>
          <Switch
            label={t('usersAdmin.fields.isDealerManager')}
            description={editing && user.id === selfId ? t('usersAdmin.fields.managerSelfHint') : t('usersAdmin.fields.managerHint')}
            checked={form.isDealerManager}
            onChange={(e) => set('isDealerManager', e.currentTarget.checked)}
            disabled={editing && user.id === selfId}
          />
          <Checkbox.Group
            label={t('usersAdmin.fields.delegations')}
            description={t('usersAdmin.fields.delegationsHint')}
            value={form.delegations}
            onChange={(value) => set('delegations', value as StandardTextCategory[])}
          >
            <Group gap="md" mt={4}>
              {TEXT_CATEGORY_VALUES.map((c) => (
                <Checkbox key={c} value={c} label={textCategoryLabel(t, c)} />
              ))}
            </Group>
          </Checkbox.Group>

          <Text fw={600} size="sm">
            {t('usersAdmin.form.sections.validity')}
          </Text>
          <SimpleGrid cols={2}>
            <TextInput label={t('usersAdmin.fields.validFrom')} type="date" value={form.validFrom} onChange={(e) => set('validFrom', e.currentTarget.value)} />
            <TextInput label={t('usersAdmin.fields.validTo')} type="date" description={t('usersAdmin.fields.validToHint')} value={form.validTo} onChange={(e) => set('validTo', e.currentTarget.value)} />
          </SimpleGrid>

          <Text fw={600} size="sm">
            {t('usersAdmin.form.sections.commercial')}
          </Text>
          <SimpleGrid cols={2}>
            <TextInput label={t('usersAdmin.fields.salesAdvisorCode')} value={form.salesAdvisorCode} onChange={(e) => set('salesAdvisorCode', e.currentTarget.value)} />
            <Checkbox mt={28} label={t('usersAdmin.fields.commissionRelevant')} checked={form.commissionRelevant} onChange={(e) => set('commissionRelevant', e.currentTarget.checked)} />
          </SimpleGrid>

          <Text fw={600} size="sm">
            {t('usersAdmin.form.sections.signature')}
          </Text>
          {editing && user.hasSignatureImage && !removeSignature && signatureUrl.data && (
            <Group align="flex-end">
              <Image src={signatureUrl.data} alt={t('usersAdmin.fields.signatureCurrent')} h={64} w="auto" fit="contain" />
              <Button variant="subtle" size="xs" color="red" onClick={() => setRemoveSignature(true)}>
                {t('usersAdmin.fields.signatureRemove')}
              </Button>
            </Group>
          )}
          <FileInput
            label={t('usersAdmin.fields.signatureImage')}
            description={t('usersAdmin.fields.signatureHint')}
            accept="image/png,image/jpeg"
            value={signatureFile}
            onChange={(file) => {
              setSignatureFile(file)
              if (file) setRemoveSignature(false)
            }}
            clearable
          />
        </Stack>
      </form>
    </FormDialog>
  )
}
