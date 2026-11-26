import { useEffect, useState } from 'react'
import { Modal } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Picker, type PickerRow } from '@nexotec/ui-kit'
import { api } from '../../../api/client'
import type { ContactPage } from '../../../api/types'
import { contactName } from '../../../shared/displayNames'
import { ContactCreateDialog } from '../../contact'

export interface ChosenContact {
  id: string
  label: string
}

interface ContactChooseDialogProps {
  opened: boolean
  title: string
  onClose: () => void
  onChosen: (contact: ChosenContact) => void
}

/**
 * «Which contact?» for the order's parties (KAN-810 criterion 1: the
 * ordering party, the invoice recipient and the driver are contacts found
 * by search). The kit's Picker over `GET /v1/contacts?q=` (the group's
 * register, ADR-014), with the create path opening the Contacts module's own
 * dialog, never a thinner copy. Whether a contact may be one of the order's
 * parties («Gesperrt», merged) is the server's answer when the order is
 * saved; this dialog decides nothing.
 */
export function ContactChooseDialog({ opened, title, onClose, onChosen }: ContactChooseDialogProps) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, 250)
  const [createOpen, setCreateOpen] = useState(false)

  useEffect(() => {
    if (opened) setQuery('')
  }, [opened])

  const search = useQuery({
    queryKey: ['service-order-contact-search', debouncedQuery],
    queryFn: () => api.get<ContactPage>(`/contacts?${new URLSearchParams({ q: debouncedQuery, limit: '10' }).toString()}`),
    enabled: opened && debouncedQuery.trim().length > 0,
  })

  const rows: PickerRow[] = (search.data?.items ?? []).map((contact) => ({
    id: contact.id,
    identifier: contact.contactNumber,
    label: contactName(contact),
  }))

  return (
    <>
      <Modal opened={opened && !createOpen} onClose={onClose} title={title}>
        <Picker
          rows={rows}
          query={query}
          onQueryChange={setQuery}
          onSelect={(row) => onChosen({ id: row.id, label: row.label })}
          loading={search.isFetching}
          inputLabel={t('serviceOrder.overview.contactSearchLabel')}
          placeholder={t('serviceOrder.overview.contactSearchPlaceholder')}
          emptyLabel={
            search.isError
              ? t('serviceOrders.create.searchError')
              : debouncedQuery.trim()
                ? t('serviceOrder.overview.noContactMatches')
                : t('serviceOrder.overview.contactSearchPlaceholder')
          }
          onCreateNew={() => setCreateOpen(true)}
          createLabel={t('contactsList.newContact')}
        />
      </Modal>
      <ContactCreateDialog
        opened={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(contact) => {
          setCreateOpen(false)
          onChosen({ id: contact.id, label: contactName(contact) })
        }}
      />
    </>
  )
}
