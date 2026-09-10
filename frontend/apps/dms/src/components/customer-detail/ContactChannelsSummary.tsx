import { Star } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Badge, purple, slate } from '@nexotec/ui-kit'
import { translatedPreferredChannelLabel } from '../../customerOptions'
import type { CustomerAddressRead, CustomerEmailRead, CustomerPhoneRead, PreferredChannel } from '../../api/types'

// FR-18 region 3 — the read view of the contact card: every current
// channel as a live `tel:` / `mailto:` link, grouped by kind, primary
// first, with the PRIMARY row of the PREFERRED kind marked. A closed or
// `doNotUse` row is neither shown here nor ever a live link (a dead number
// behind a `tel:` link gets dialled) — those live in the editor's
// former-rows toggle below, struck through and inert.
//
// The editors (RepeatableRowGroup) still own add / edit / close / consent;
// this is the at-a-glance "how do I reach them" surface alongside them.

/** email -> email rows, phone / whatsapp -> phone rows, post -> address.
 * `message` (legacy SMS, D-21) maps nowhere. */
const PREFERRED_KIND: Record<PreferredChannel, 'phone' | 'email' | 'address' | null> = {
  email: 'email',
  phone: 'phone',
  whatsapp: 'phone',
  post: 'address',
  message: null,
}

function isCurrent(row: { validTo?: string | null; doNotUse?: boolean | null }): boolean {
  if (row.doNotUse) return false
  return !row.validTo || new Date(row.validTo).getTime() > Date.now()
}

const primaryFirst = <T extends { isPrimary: boolean }>(rows: T[]): T[] =>
  [...rows].sort((a, b) => Number(b.isPrimary) - Number(a.isPrimary))

interface ContactChannelsSummaryProps {
  phones: CustomerPhoneRead[]
  emails: CustomerEmailRead[]
  address: CustomerAddressRead | null
  preferredChannel: PreferredChannel | null
}

/** The preferred channel as a header badge — legible even when the
 * customer has no row of that kind, which is a real state and the one
 * where a row-level mark alone tells you nothing. */
export function PreferredChannelBadge({ preferredChannel }: { preferredChannel: PreferredChannel | null }) {
  const { t } = useTranslation()
  if (!preferredChannel) {
    return (
      <span style={{ fontSize: 11, color: slate[4] }}>{t('customerDetail.contactPoints.noPreferred')}</span>
    )
  }
  return (
    <Badge tone="purple" icon={<Star size={12} fill={slate[9]} />}>
      {translatedPreferredChannelLabel(t, preferredChannel)}
    </Badge>
  )
}

function ChannelLink({ href, text, preferred, ariaPreferred }: { href: string; text: string; preferred: boolean; ariaPreferred: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '2px 0' }}>
      {preferred && <Star size={13} fill={purple[6]} color={purple[6]} aria-label={ariaPreferred} />}
      <a href={href} style={{ fontSize: 13, color: purple[6] }}>
        {text}
      </a>
    </div>
  )
}

export function ContactChannelsSummary({ phones, emails, address, preferredChannel }: ContactChannelsSummaryProps) {
  const { t } = useTranslation()
  const prefKind = preferredChannel ? PREFERRED_KIND[preferredChannel] : null
  const ariaPreferred = t('customerDetail.contactPoints.preferredMark')

  const currentPhones = primaryFirst(phones.filter(isCurrent))
  const currentEmails = primaryFirst(emails.filter(isCurrent))
  // The full address already has its own card; it only appears here to
  // carry the preferred mark when `post` is the chosen channel.
  const currentAddress = prefKind === 'address' && address && isCurrent(address) ? address : null

  const nothing = currentPhones.length === 0 && currentEmails.length === 0 && !currentAddress

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2, paddingBottom: 8 }}>
      {nothing && <span style={{ fontSize: 13, color: slate[4], fontStyle: 'italic' }}>{t('customerDetail.contactPoints.none')}</span>}

      {currentPhones.map((p) => (
        <ChannelLink
          key={p.id}
          href={`tel:${p.phoneE164.replace(/\s+/g, '')}`}
          text={p.phoneE164}
          preferred={prefKind === 'phone' && p.isPrimary}
          ariaPreferred={ariaPreferred}
        />
      ))}
      {currentEmails.map((e) => (
        <ChannelLink
          key={e.id}
          href={`mailto:${e.emailAddress}`}
          text={e.emailAddress}
          preferred={prefKind === 'email' && e.isPrimary}
          ariaPreferred={ariaPreferred}
        />
      ))}
      {currentAddress && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '2px 0' }}>
          {prefKind === 'address' && currentAddress.isPrimary && (
            <Star size={13} fill={purple[6]} color={purple[6]} aria-label={ariaPreferred} />
          )}
          {/* Addresses get no scheme — a maps link is a different decision nobody has made. */}
          <span style={{ fontSize: 13, color: slate[7] }}>
            {`${currentAddress.addressStreet} ${currentAddress.addressHouseNumber}, ${currentAddress.addressPostalCode} ${currentAddress.addressLocality}`}
          </span>
        </div>
      )}
    </div>
  )
}
