import { Text, UnstyledButton } from '@mantine/core'
import { useTranslation } from 'react-i18next'

/**
 * KAN-570 — the stock car's number beside the car on an offer or a contract
 * («Lagernummer S-000123»), the copy KAN-157 stores on the deal. The yard and
 * the purchasing desk know the car by it. It opens the stock detail as an
 * overlay (ADR-059: the deal underneath is never navigated away from); a
 * host that cannot open it shows the number alone.
 */
export function StockNumberLink({ stockNumber, onOpen }: { stockNumber: string; onOpen?: () => void }) {
  const { t } = useTranslation()
  const text = t('offerWorkspace.vehicle.stockNumber', { stockNumber })
  if (!onOpen) {
    return (
      <Text size="sm" c="dimmed" data-testid="deal-stock-number">
        {text}
      </Text>
    )
  }
  return (
    <UnstyledButton onClick={onOpen} data-testid="deal-stock-number">
      <Text size="sm" fw={600} c="purple">
        {text}
      </Text>
    </UnstyledButton>
  )
}
