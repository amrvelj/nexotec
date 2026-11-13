import { Alert, Button, Group, Stack, Text } from '@mantine/core'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import type { CreateOfferFromValuationError } from '../hooks/useCreateOfferFromValuation'

/** KAN-569 — a refused «Offerte aus dieser Bewertung erstellen», said where
 * the user acted (ADR-035). When the offer exists and only taking the
 * valuation over as its trade-in was refused, it names that offer and opens
 * it; the action itself, clicked again, continues the same offer. */
export function CreateOfferFromValuationAlert({
  error,
  onDismiss,
}: {
  error: CreateOfferFromValuationError
  onDismiss: () => void
}) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const offer = error.offer
  return (
    <Alert color="red" role="alert" withCloseButton closeButtonLabel={t('common.dismiss')} onClose={onDismiss}>
      <Stack gap="xs">
        <Text size="sm">{error.text}</Text>
        {offer && (
          <Group>
            <Button size="xs" variant="default" onClick={() => navigate(`/sales/offers/${offer.id}`)}>
              {t('valuationDetail.errors.openCreatedOffer')}
            </Button>
          </Group>
        )}
      </Stack>
    </Alert>
  )
}
