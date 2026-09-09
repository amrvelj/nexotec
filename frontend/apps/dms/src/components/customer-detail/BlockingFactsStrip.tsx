import { Alert, Stack } from '@mantine/core'
import { Ban, Lock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { CustomerRead } from '../../api/types'

/**
 * FR-18 region 1 — "the blocking facts sit in a STRIP ABOVE THE CARDS, not
 * a field in the middle of one. A fact that changes what you may do cannot
 * be discovered by scrolling." KAN-44.
 *
 * `creditBlock` (red) carries `creditBlockReason` verbatim; `do_not_contact`
 * (amber) carries a fixed note. Both render when both are true, blocked
 * first. Neither true renders NOTHING — not an empty "not blocked" row
 * (the component returns `null`, so there is no DOM node at all).
 *
 * The prototype's own strip (`44-screens-details.js` `overview()`):
 * `alert alert-danger` for blocked, `alert alert-warn` for dnc, above the
 * five-stat row and every card.
 */
export function BlockingFactsStrip({ customer }: { customer: CustomerRead }) {
  const { t } = useTranslation()
  const isBlocked = customer.creditBlock
  const isDoNotContact = customer.lifecycleStatus === 'do_not_contact'

  if (!isBlocked && !isDoNotContact) return null

  return (
    <Stack gap="xs">
      {isBlocked && (
        <Alert
          color="red"
          icon={<Lock size={18} />}
          title={t('customerDetail.overview.blockingStrip.creditBlockTitle')}
        >
          {customer.creditBlockReason ?? ''}
        </Alert>
      )}
      {isDoNotContact && (
        <Alert
          color="orange"
          icon={<Ban size={18} />}
          title={t('customerDetail.overview.blockingStrip.doNotContactTitle')}
        >
          {t('customerDetail.overview.blockingStrip.doNotContactNote')}
        </Alert>
      )}
    </Stack>
  )
}
