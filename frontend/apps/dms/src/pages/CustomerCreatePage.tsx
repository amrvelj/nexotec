import { Container, Stack, Title } from '@mantine/core'
import { useNavigate } from 'react-router-dom'
import { useSetBreadcrumb } from '@nexotec/ui-kit'
import { CustomerCreateFlow } from '../components/CustomerCreateFlow'

// Page-level chrome only — all wizard state, validation, and the create
// call itself live in CustomerCreateFlow so the same component can be
// embedded elsewhere (e.g. a future "new customer" step inside an Offer
// flow) without dragging this page's layout along with it.
export function CustomerCreatePage() {
  useSetBreadcrumb(['Master Data', 'Customers', 'New customer'])
  const navigate = useNavigate()

  return (
    <Container py="xl" size="sm">
      <Stack gap="xl">
        <Title order={2}>New customer</Title>
        <CustomerCreateFlow
          onSuccess={(customer) => navigate(`/customers/${customer.id}`)}
          onCancel={() => navigate('/customers')}
          onOpenExisting={(customerId) => navigate(`/customers/${customerId}`)}
        />
      </Stack>
    </Container>
  )
}
