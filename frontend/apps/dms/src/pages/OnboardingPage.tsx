import { useMemo, useState } from 'react'
import { Alert, Button, Checkbox, Group, MultiSelect, Paper, Radio, Select, SimpleGrid, Stack, Table, Text, TextInput, Title } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { CheckCircle2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Wizard, useSetBreadcrumb } from '@nexotec/ui-kit'
import { api } from '../api/client'
import type { AccessRole, DealerGroupOptionList, OnboardingRequest, OnboardingResult, UserRole } from '../api/types'
import { useListOptions } from '../components/business/useListOptions'
import { CANTON_OPTIONS } from '../customerOptions'
import { FRANCHISE_TYPES } from '../businessOptions'
import { GRANTABLE_ACCESS_ROLES, USER_ROLE_VALUES, accessRoleLabel, userRoleLabel } from '../userOptions'
import { apiRefusalText } from '../utils/apiRefusal'

/** The module entitlements Nexotec licenses (D-A-04; `ModuleCode`). */
export const ONBOARDING_MODULES = ['sales', 'aftersales', 'parts', 'finance', 'reporting'] as const
type ModuleCode = (typeof ONBOARDING_MODULES)[number]

interface Form {
  groupMode: 'new' | 'existing'
  newGroupName: string
  dealerGroupId: string | null
  legalName: string
  tradeName: string
  franchiseType: string | null
  legalForm: string | null
  uid: string
  street: string
  houseNumber: string
  postalCode: string
  locality: string
  canton: string | null
  phone: string
  modules: ModuleCode[]
  firstName: string
  lastName: string
  email: string
  role: UserRole | null
  accessRoles: AccessRole[]
}

const EMPTY: Form = {
  groupMode: 'new',
  newGroupName: '',
  dealerGroupId: null,
  legalName: '',
  tradeName: '',
  franchiseType: null,
  legalForm: null,
  uid: '',
  street: '',
  houseNumber: '',
  postalCode: '',
  locality: '',
  canton: null,
  phone: '',
  modules: ['sales'],
  firstName: '',
  lastName: '',
  email: '',
  role: null,
  accessRoles: [],
}

const filled = (value: string | null) => value !== null && value.trim() !== ''

/**
 * Platform Administration › Onboarding (KAN-210; Dealer Administration
 * D-A-06, FR-A-24/25): Nexotec staff onboard a Betrieb in five steps —
 * group, legal entity, modules, first manager, review — and one atomic
 * `POST /v1/platform/onboarding`. Nexotec sets nothing commercial: no VAT
 * rate, no bank account; the manager configures those in Dealer
 * Administration after accepting the invitation. Staff-only at the API; the
 * screen is never role-hidden (A-RULE-7).
 */
export function OnboardingPage() {
  const { t } = useTranslation()
  useSetBreadcrumb([t('shell.nav.administration'), t('onboarding.title')])
  const [step, setStep] = useState(0)
  const [form, setForm] = useState<Form>(EMPTY)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<OnboardingResult | null>(null)
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((current) => ({ ...current, [key]: value }))

  const groups = useQuery({
    queryKey: ['platform-dealer-groups'],
    queryFn: () => api.get<DealerGroupOptionList>('/platform/dealer-groups'),
  })
  const legalForms = useListOptions('legal_form')

  const steps = useMemo(
    () => [
      { id: 'group', label: t('onboarding.steps.group') },
      { id: 'legal', label: t('onboarding.steps.legal') },
      { id: 'modules', label: t('onboarding.steps.modules') },
      { id: 'manager', label: t('onboarding.steps.manager') },
      { id: 'review', label: t('onboarding.steps.review') },
    ],
    [t]
  )

  const stepComplete = [
    form.groupMode === 'new' ? filled(form.newGroupName) : form.dealerGroupId !== null,
    [form.legalName, form.franchiseType, form.uid, form.street, form.postalCode, form.locality, form.canton, form.phone].every(filled),
    true,
    [form.firstName, form.lastName, form.email, form.role].every(filled) && form.accessRoles.length > 0,
    true,
  ]

  const submit = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const body: OnboardingRequest = {
        group: form.groupMode === 'new' ? { newGroupName: form.newGroupName.trim() } : { dealerGroupId: form.dealerGroupId },
        legalEntity: {
          legalName: form.legalName.trim(),
          tradeName: form.tradeName.trim() || null,
          franchiseType: form.franchiseType,
          legalForm: form.legalForm,
          uid: form.uid.trim(),
          address: {
            street: form.street.trim(),
            houseNumber: form.houseNumber.trim() || null,
            postalCode: form.postalCode.trim(),
            locality: form.locality.trim(),
            canton: form.canton,
          },
          // E.164 at the API: «+41 44 123 45 67» as typed is sent as +41441234567.
          phone: form.phone.replace(/[\s\-/().]/g, ''),
        },
        entitlements: form.modules,
        manager: {
          firstName: form.firstName.trim(),
          lastName: form.lastName.trim(),
          email: form.email.trim(),
          role: form.role,
          accessRoles: form.accessRoles,
        },
      }
      setResult(await api.post<OnboardingResult>('/platform/onboarding', body))
    } catch (err) {
      setError(apiRefusalText(t, err, 'onboarding.errors.generic'))
    } finally {
      setSubmitting(false)
    }
  }

  if (result) {
    return (
      <Stack gap="md" data-testid="onboarding-success">
        <Title order={2}>{t('onboarding.title')}</Title>
        <Alert color="green" icon={<CheckCircle2 size={20} />} title={t('onboarding.success.title', { name: form.legalName })}>
          <Stack gap={4}>
            <Text size="sm">{t('onboarding.success.pending')}</Text>
            <Text size="sm">
              {result.invitationDelivered
                ? t('onboarding.success.invited', { email: result.managerEmail })
                : t('onboarding.success.invitationFailed', { email: result.managerEmail })}
            </Text>
            <Text size="sm">{t('onboarding.success.nextSteps')}</Text>
          </Stack>
        </Alert>
        <Group>
          <Button
            variant="light"
            onClick={() => {
              setResult(null)
              setForm(EMPTY)
              setStep(0)
            }}
          >
            {t('onboarding.success.another')}
          </Button>
        </Group>
      </Stack>
    )
  }

  const groupName =
    form.groupMode === 'new' ? form.newGroupName : (groups.data?.items.find((g) => g.id === form.dealerGroupId)?.name ?? '—')
  const reviewRows: [string, string][] = [
    [t('onboarding.review.group'), groupName],
    [t('business.legal.legalName'), form.legalName],
    [t('business.legal.uid'), form.uid],
    [t('onboarding.review.seat'), [`${form.street} ${form.houseNumber}`.trim(), `${form.postalCode} ${form.locality}`, form.canton ?? ''].filter(Boolean).join(', ')],
    [t('onboarding.steps.modules'), form.modules.length ? form.modules.map((m) => t(`onboarding.modules.${m}`)).join(', ') : t('onboarding.review.noModules')],
    [t('onboarding.review.manager'), `${form.firstName} ${form.lastName} · ${form.email}`],
  ]

  return (
    <Stack gap="md">
      <div>
        <Title order={2}>{t('onboarding.title')}</Title>
        <Text c="dimmed" fz="sm">
          {t('onboarding.subtitle')}
        </Text>
      </div>
      <Paper withBorder p="lg" radius="md">
        <Wizard
          steps={steps}
          activeIndex={step}
          onBack={() => setStep((s) => Math.max(0, s - 1))}
          onNext={() => (step === steps.length - 1 ? void submit() : setStep((s) => s + 1))}
          onCancel={() => {
            setForm(EMPTY)
            setStep(0)
            setError(null)
          }}
          nextLabel={t('onboarding.actions.next')}
          backLabel={t('onboarding.actions.back')}
          cancelLabel={t('onboarding.actions.reset')}
          submitLabel={t('onboarding.actions.submit')}
          nextDisabled={!stepComplete[step]}
          submitting={submitting}
          error={error}
        >
          {step === 0 && (
            <Stack gap="sm">
              <Radio.Group value={form.groupMode} onChange={(v) => set('groupMode', v as Form['groupMode'])} label={t('onboarding.group.mode')}>
                <Group mt="xs">
                  <Radio value="new" label={t('onboarding.group.new')} />
                  <Radio value="existing" label={t('onboarding.group.existing')} />
                </Group>
              </Radio.Group>
              {form.groupMode === 'new' ? (
                <TextInput label={t('onboarding.group.newName')} value={form.newGroupName} onChange={(e) => set('newGroupName', e.currentTarget.value)} required />
              ) : (
                <Select
                  label={t('onboarding.group.pick')}
                  data={(groups.data?.items ?? []).map((g) => ({ value: g.id, label: g.name }))}
                  value={form.dealerGroupId}
                  onChange={(v) => set('dealerGroupId', v)}
                  searchable
                  nothingFoundMessage={t('common.noMatches')}
                  required
                />
              )}
            </Stack>
          )}
          {step === 1 && (
            <Stack gap="sm">
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <TextInput label={t('business.legal.legalName')} value={form.legalName} onChange={(e) => set('legalName', e.currentTarget.value)} required />
                <TextInput label={t('business.legal.tradeName')} value={form.tradeName} onChange={(e) => set('tradeName', e.currentTarget.value)} />
                <Select
                  label={t('business.legal.legalForm')}
                  data={legalForms.options}
                  value={form.legalForm}
                  onChange={(v) => set('legalForm', v)}
                  clearable
                />
                <Select
                  label={t('business.legal.franchiseType')}
                  data={FRANCHISE_TYPES.map((v) => ({ value: v, label: t(`business.legal.franchiseTypes.${v}`) }))}
                  value={form.franchiseType}
                  onChange={(v) => set('franchiseType', v)}
                  required
                />
                <TextInput label={t('business.legal.uid')} placeholder="CHE-123.456.789" value={form.uid} onChange={(e) => set('uid', e.currentTarget.value)} required />
                <TextInput label={t('business.addresses.phone')} placeholder="+41 44 123 45 67" value={form.phone} onChange={(e) => set('phone', e.currentTarget.value)} required />
              </SimpleGrid>
              <Text fw={500} fz="sm">
                {t('onboarding.legal.seat')}
              </Text>
              <SimpleGrid cols={{ base: 2, sm: 4 }}>
                <TextInput label={t('business.addresses.street')} value={form.street} onChange={(e) => set('street', e.currentTarget.value)} required />
                <TextInput label={t('business.addresses.houseNumber')} value={form.houseNumber} onChange={(e) => set('houseNumber', e.currentTarget.value)} />
                <TextInput label={t('business.addresses.postalCode')} value={form.postalCode} onChange={(e) => set('postalCode', e.currentTarget.value)} required />
                <TextInput label={t('business.addresses.locality')} value={form.locality} onChange={(e) => set('locality', e.currentTarget.value)} required />
                <Select label={t('business.addresses.canton')} data={CANTON_OPTIONS} value={form.canton} onChange={(v) => set('canton', v)} searchable required />
              </SimpleGrid>
            </Stack>
          )}
          {step === 2 && (
            <Stack gap="xs">
              <Text fz="sm" c="dimmed">
                {t('onboarding.modulesHint')}
              </Text>
              {ONBOARDING_MODULES.map((module) => (
                <Checkbox
                  key={module}
                  label={t(`onboarding.modules.${module}`)}
                  checked={form.modules.includes(module)}
                  onChange={(e) =>
                    set('modules', e.currentTarget.checked ? [...form.modules, module] : form.modules.filter((m) => m !== module))
                  }
                />
              ))}
            </Stack>
          )}
          {step === 3 && (
            <SimpleGrid cols={{ base: 1, sm: 2 }}>
              <TextInput label={t('usersAdmin.fields.firstName')} value={form.firstName} onChange={(e) => set('firstName', e.currentTarget.value)} required />
              <TextInput label={t('usersAdmin.fields.lastName')} value={form.lastName} onChange={(e) => set('lastName', e.currentTarget.value)} required />
              <TextInput label={t('usersAdmin.fields.email')} type="email" value={form.email} onChange={(e) => set('email', e.currentTarget.value)} required />
              <Select
                label={t('usersAdmin.fields.jobTitle')}
                data={USER_ROLE_VALUES.map((r) => ({ value: r, label: userRoleLabel(t, r) }))}
                value={form.role}
                onChange={(v) => set('role', v as UserRole | null)}
                required
              />
              <MultiSelect
                label={t('usersAdmin.fields.primaryRole')}
                data={GRANTABLE_ACCESS_ROLES.map((r) => ({ value: r, label: accessRoleLabel(t, r) }))}
                value={form.accessRoles}
                onChange={(v) => set('accessRoles', v as AccessRole[])}
                required
              />
              <Text fz="sm" c="dimmed" style={{ alignSelf: 'end' }}>
                {t('onboarding.manager.hint')}
              </Text>
            </SimpleGrid>
          )}
          {step === 4 && (
            <Stack gap="sm">
              <Table>
                <Table.Tbody>
                  {reviewRows.map(([label, value]) => (
                    <Table.Tr key={label}>
                      <Table.Th w={220}>{label}</Table.Th>
                      <Table.Td>{value}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
              <Text fz="sm" c="dimmed">
                {t('onboarding.review.nothingCommercial')}
              </Text>
            </Stack>
          )}
        </Wizard>
      </Paper>
    </Stack>
  )
}
