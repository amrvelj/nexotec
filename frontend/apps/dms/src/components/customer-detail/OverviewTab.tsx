import { useState } from 'react'
import { Checkbox, Group, Select, SimpleGrid, Stack, Text, TextInput } from '@mantine/core'
import { InlineEditField, KeyValueRow, OverviewCard, purple, radius, slate } from '@nexotec/ui-kit'
import {
  EMAIL_TYPE_OPTIONS,
  LANGUAGE_OPTIONS,
  LEGAL_FORM_OPTIONS,
  LIFECYCLE_OPTIONS,
  PHONE_TYPE_OPTIONS,
  PREFERRED_CHANNEL_OPTIONS,
  SALUTATION_OPTIONS,
  SOURCE_OPTIONS,
} from '../../customerOptions'
import { formatDate } from '../../utils/format'
import { ContactPointsEditor } from './ContactPointsEditor'
import { PhoneInput } from '../PhoneInput'
import type { CustomerEmailRead, CustomerPhoneRead, CustomerRead, CustomerUpdateInput, EmailType, PhoneType } from '../../api/types'

interface OverviewTabProps {
  customer: CustomerRead
  phones: CustomerPhoneRead[]
  emails: CustomerEmailRead[]
  onSaveField: (patch: Partial<CustomerUpdateInput>) => Promise<void>
  isConflict: (err: unknown) => boolean
  onReload: () => void
  onCreatePhone: (row: { type: PhoneType; value: string }) => Promise<void>
  onUpdatePhone: (id: string, patch: { type?: PhoneType; value?: string; isPrimary?: boolean }) => Promise<void>
  onDeletePhone: (id: string) => Promise<void>
  onCreateEmail: (row: { type: EmailType; value: string }) => Promise<void>
  onUpdateEmail: (id: string, patch: { type?: EmailType; value?: string; isPrimary?: boolean }) => Promise<void>
  onDeleteEmail: (id: string) => Promise<void>
}

interface FieldProps {
  onSaveField: OverviewTabProps['onSaveField']
  isConflict: OverviewTabProps['isConflict']
  onReload: OverviewTabProps['onReload']
}

function TextField({
  label,
  value,
  patchKey,
  onSaveField,
  isConflict,
  onReload,
}: FieldProps & { label: string; value: string | null; patchKey: keyof CustomerUpdateInput }) {
  return (
    <KeyValueRow label={label}>
      <InlineEditField
        value={value ?? ''}
        isEmpty={!value}
        onSave={(raw) => onSaveField({ [patchKey]: raw.trim() || null } as Partial<CustomerUpdateInput>)}
        isConflict={isConflict}
        onReload={onReload}
      />
    </KeyValueRow>
  )
}

function DateField({
  label,
  value,
  patchKey,
  onSaveField,
  isConflict,
  onReload,
}: FieldProps & { label: string; value: string | null; patchKey: keyof CustomerUpdateInput }) {
  return (
    <KeyValueRow label={label}>
      <InlineEditField
        value={value ? formatDate(value) : ''}
        isEmpty={!value}
        editValue={value ?? ''}
        onSave={(raw) => onSaveField({ [patchKey]: raw || null } as Partial<CustomerUpdateInput>)}
        isConflict={isConflict}
        onReload={onReload}
        renderEditor={({ value: v, onCommit, autoFocus }) => (
          <input
            type="date"
            defaultValue={v}
            autoFocus={autoFocus}
            onChange={(e) => onCommit(e.currentTarget.value)}
            style={{ font: 'inherit', border: `1px solid ${slate[3]}`, borderRadius: radius.sm, padding: '2px 6px' }}
          />
        )}
      />
    </KeyValueRow>
  )
}

function SelectField({
  label,
  value,
  options,
  patchKey,
  clearable,
  onSaveField,
  isConflict,
  onReload,
}: FieldProps & { label: string; value: string | null; options: { value: string; label: string }[]; patchKey: keyof CustomerUpdateInput; clearable?: boolean }) {
  const displayLabel = options.find((o) => o.value === value)?.label
  return (
    <KeyValueRow label={label}>
      <InlineEditField
        value={displayLabel ?? ''}
        isEmpty={!value}
        editValue={value ?? ''}
        onSave={(raw) => onSaveField({ [patchKey]: raw || null } as Partial<CustomerUpdateInput>)}
        isConflict={isConflict}
        onReload={onReload}
        renderEditor={({ value: v, onCommit, autoFocus }) => (
          <Select
            data={options}
            value={v || null}
            onChange={(next) => onCommit(next ?? '')}
            autoFocus={autoFocus}
            clearable={clearable}
            size="xs"
            comboboxProps={{ withinPortal: true }}
          />
        )}
      />
    </KeyValueRow>
  )
}

function AddressField({ customer, onSaveField, isConflict, onReload }: FieldProps & { customer: CustomerRead }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({
    street: customer.address?.street ?? '',
    houseNumber: customer.address?.houseNumber ?? '',
    postalCode: customer.address?.postalCode ?? '',
    locality: customer.address?.locality ?? '',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const startEdit = () => {
    setDraft({
      street: customer.address?.street ?? '',
      houseNumber: customer.address?.houseNumber ?? '',
      postalCode: customer.address?.postalCode ?? '',
      locality: customer.address?.locality ?? '',
    })
    setError(null)
    setEditing(true)
  }

  const [conflict, setConflict] = useState(false)

  const save = async () => {
    setSaving(true)
    setError(null)
    setConflict(false)
    try {
      const hasAny = draft.street || draft.houseNumber || draft.postalCode || draft.locality
      await onSaveField({ address: hasAny ? { ...draft, country: customer.address?.country ?? 'CH' } : null })
      setEditing(false)
    } catch (err) {
      const isConflictErr = isConflict(err)
      setConflict(isConflictErr)
      setError(isConflictErr ? 'Someone else changed this in the meantime.' : err instanceof Error ? err.message : 'Failed to save.')
    } finally {
      setSaving(false)
    }
  }

  if (editing) {
    return (
      <div style={{ padding: `8px 0` }}>
        <Stack gap="xs">
          <Group grow gap="xs">
            <TextInput size="xs" label="Street" value={draft.street} onChange={(e) => setDraft({ ...draft, street: e.currentTarget.value })} />
            <TextInput
              size="xs"
              label="House no."
              value={draft.houseNumber}
              onChange={(e) => setDraft({ ...draft, houseNumber: e.currentTarget.value })}
            />
          </Group>
          <Group grow gap="xs">
            <TextInput
              size="xs"
              label="Postal code"
              value={draft.postalCode}
              onChange={(e) => setDraft({ ...draft, postalCode: e.currentTarget.value })}
            />
            <TextInput size="xs" label="Locality" value={draft.locality} onChange={(e) => setDraft({ ...draft, locality: e.currentTarget.value })} />
          </Group>
          {error && (
            <Group gap={6}>
              <Text size="xs" c="red">
                {error}
              </Text>
              {conflict && (
                <button
                  type="button"
                  onClick={onReload}
                  style={{ border: 'none', background: 'none', color: purple[6], cursor: 'pointer', fontWeight: 600, fontSize: 12, padding: 0 }}
                >
                  Reload
                </button>
              )}
            </Group>
          )}
          <Group gap="xs">
            <button
              type="button"
              onClick={() => void save()}
              disabled={saving}
              style={{ fontSize: 12, fontWeight: 600, color: '#fff', backgroundColor: purple[6], border: 'none', borderRadius: radius.sm, padding: '4px 10px', cursor: 'pointer' }}
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              disabled={saving}
              style={{ fontSize: 12, fontWeight: 600, color: slate[6], background: 'none', border: 'none', cursor: 'pointer' }}
            >
              Cancel
            </button>
          </Group>
        </Stack>
      </div>
    )
  }

  const addr = customer.address
  const line = addr ? `${addr.street} ${addr.houseNumber}, ${addr.postalCode} ${addr.locality}` : ''
  return (
    <KeyValueRow label="Address">
      <span onClick={startEdit} style={{ cursor: 'pointer', fontStyle: addr ? undefined : 'italic', color: addr ? undefined : slate[3] }}>
        {addr ? line : 'Not set'}
      </span>
    </KeyValueRow>
  )
}

export function OverviewTab({
  customer,
  phones,
  emails,
  onSaveField,
  isConflict,
  onReload,
  onCreatePhone,
  onUpdatePhone,
  onDeletePhone,
  onCreateEmail,
  onUpdateEmail,
  onDeleteEmail,
}: OverviewTabProps) {
  const fieldProps: FieldProps = { onSaveField, isConflict, onReload }

  return (
    <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
      <OverviewCard title="Identity">
        {customer.customerType === 'individual' ? (
          <>
            <SelectField label="Salutation" value={customer.salutation} options={SALUTATION_OPTIONS} patchKey="salutation" clearable {...fieldProps} />
            <TextField label="First name" value={customer.firstName} patchKey="firstName" {...fieldProps} />
            <TextField label="Last name" value={customer.lastName} patchKey="lastName" {...fieldProps} />
            <DateField label="Date of birth" value={customer.birthDate} patchKey="birthDate" {...fieldProps} />
            <TextField label="Nationality" value={customer.nationality} patchKey="nationality" {...fieldProps} />
          </>
        ) : (
          <>
            <TextField label="Company name" value={customer.companyName} patchKey="companyName" {...fieldProps} />
            <SelectField label="Legal form" value={customer.legalForm} options={LEGAL_FORM_OPTIONS} patchKey="legalForm" clearable {...fieldProps} />
          </>
        )}
        <SelectField label="Correspondence language" value={customer.language} options={LANGUAGE_OPTIONS} patchKey="language" {...fieldProps} />
        <SelectField
          label="Preferred channel"
          value={customer.preferredChannel}
          options={PREFERRED_CHANNEL_OPTIONS}
          patchKey="preferredChannel"
          clearable
          {...fieldProps}
        />
      </OverviewCard>

      <OverviewCard title="Status">
        <SelectField label="Lifecycle status" value={customer.lifecycleStatus} options={LIFECYCLE_OPTIONS} patchKey="lifecycleStatus" {...fieldProps} />
        <SelectField label="Source" value={customer.source} options={SOURCE_OPTIONS} patchKey="source" clearable {...fieldProps} />
        <KeyValueRow label="Marketing consent">
          <Checkbox
            checked={customer.marketingConsent}
            onChange={(e) => void onSaveField({ marketingConsent: e.currentTarget.checked })}
            styles={{ root: { display: 'inline-flex' } }}
          />
        </KeyValueRow>
      </OverviewCard>

      <OverviewCard title="Address">
        <AddressField customer={customer} {...fieldProps} />
        <KeyValueRow label="Canton">
          <span style={{ fontStyle: customer.address?.canton ? undefined : 'italic', color: customer.address?.canton ? undefined : slate[3] }}>
            {customer.address?.canton ?? 'Not set'}
          </span>
        </KeyValueRow>
      </OverviewCard>

      <OverviewCard title="Contact points">
        <Stack gap="md">
          <ContactPointsEditor<PhoneType>
            label="Phone numbers"
            addLabel="Add phone"
            typeOptions={PHONE_TYPE_OPTIONS}
            rows={phones.map((p) => ({ id: p.id, type: p.phoneType, value: p.phoneE164, isPrimary: p.isPrimary }))}
            newRowType="mobile"
            renderValueEditor={(value, onChange) => <PhoneInput label="Country" value={value} onChange={onChange} />}
            onCreate={onCreatePhone}
            onUpdate={onUpdatePhone}
            onDelete={onDeletePhone}
          />
          <ContactPointsEditor<EmailType>
            label="Email addresses"
            addLabel="Add email"
            typeOptions={EMAIL_TYPE_OPTIONS}
            rows={emails.map((e) => ({ id: e.id, type: e.emailType, value: e.emailAddress, isPrimary: e.isPrimary }))}
            newRowType="private"
            renderValueEditor={(value, onChange, autoFocus) => (
              <TextInput size="xs" type="email" value={value} onChange={(e) => onChange(e.currentTarget.value)} autoFocus={autoFocus} />
            )}
            onCreate={onCreateEmail}
            onUpdate={onUpdateEmail}
            onDelete={onDeleteEmail}
          />
        </Stack>
      </OverviewCard>

      <OverviewCard title="Record">
        <KeyValueRow label="Customer number">
          <span style={{ fontFamily: 'ui-monospace, SF Mono, Menlo, monospace' }}>{customer.customerNumber}</span>
        </KeyValueRow>
        <KeyValueRow label="Created">{formatDate(customer.createdAt)}</KeyValueRow>
        <KeyValueRow label="Last changed">{formatDate(customer.updatedAt)}</KeyValueRow>
      </OverviewCard>
    </SimpleGrid>
  )
}
