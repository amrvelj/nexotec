import type {
  CustomerLifecycleStatus,
  CustomerSource,
  EmailType,
  Language,
  LegalForm,
  PhoneType,
  PreferredChannel,
  Salutation,
} from './api/types'

export const LANGUAGE_OPTIONS: { value: Language; label: string }[] = [
  { value: 'de', label: 'Deutsch' },
  { value: 'fr', label: 'Français' },
  { value: 'it', label: 'Italiano' },
  { value: 'en', label: 'English' },
]

// 'merged' is excluded — neither CustomerCreate nor CustomerUpdate accepts
// it; a customer can only reach that status via POST /customers/{id}/merge.
export const LIFECYCLE_OPTIONS: { value: CustomerLifecycleStatus; label: string }[] = [
  { value: 'prospect', label: 'Prospect' },
  { value: 'active', label: 'Active' },
  { value: 'inactive', label: 'Inactive' },
  { value: 'do_not_contact', label: 'Do not contact' },
]

export const SOURCE_OPTIONS: { value: CustomerSource; label: string }[] = [
  { value: 'walk_in', label: 'Walk-in' },
  { value: 'phone', label: 'Phone' },
  { value: 'web_lead', label: 'Web lead' },
  { value: 'marketplace', label: 'Marketplace' },
  { value: 'other', label: 'Other' },
]

export const SALUTATION_OPTIONS: { value: Salutation; label: string }[] = [
  { value: 'herr', label: 'Herr' },
  { value: 'frau', label: 'Frau' },
  { value: 'firma', label: 'Firma' },
  { value: 'neutral', label: 'Neutral' },
]

export const LEGAL_FORM_OPTIONS: { value: LegalForm; label: string }[] = [
  { value: 'ag', label: 'AG' },
  { value: 'gmbh', label: 'GmbH' },
  { value: 'einzelfirma', label: 'Einzelfirma' },
  { value: 'verein', label: 'Verein' },
  { value: 'genossenschaft', label: 'Genossenschaft' },
  { value: 'weitere', label: 'Weitere' },
]

export const PREFERRED_CHANNEL_OPTIONS: { value: PreferredChannel; label: string }[] = [
  { value: 'mail', label: 'Mail' },
  { value: 'call', label: 'Call' },
  { value: 'message', label: 'Message' },
  { value: 'letter', label: 'Letter' },
]

export const PHONE_TYPE_OPTIONS: { value: PhoneType; label: string }[] = [
  { value: 'mobile', label: 'Mobile' },
  { value: 'private', label: 'Private' },
  { value: 'office', label: 'Office' },
]

export const EMAIL_TYPE_OPTIONS: { value: EmailType; label: string }[] = [
  { value: 'private', label: 'Private' },
  { value: 'business', label: 'Business' },
]
