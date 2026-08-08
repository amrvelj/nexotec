import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Alert,
  Button,
  Container,
  Group,
  Loader,
  Stack,
  Table,
  TextInput,
  Title,
} from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { CustomerTypeBadge, LanguageBadge, LifecycleStatusBadge } from '@nexotec/ui-kit'
import { api, ApiError } from '../api/client'
import type { CustomerPage, CustomerRead } from '../api/types'
import { useAuth } from '../auth/AuthContext'

export function CustomersListPage() {
  const { user, logout } = useAuth()
  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, 300)
  const [items, setItems] = useState<CustomerRead[]>([])
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback((q: string, cursorParam: string | null) => {
    setLoading(true)
    setError(null)
    const params = new URLSearchParams()
    if (q) params.set('q', q)
    if (cursorParam) params.set('cursor', cursorParam)
    api
      .get<CustomerPage>(`/customers?${params.toString()}`)
      .then((page) => {
        setItems(page.items)
        setNextCursor(page.nextCursor)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load customers.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load(debouncedQuery, null)
  }, [debouncedQuery, load])

  return (
    <Container py="xl">
      <Stack gap="md">
        <Group justify="space-between">
          <Title order={2}>Customers</Title>
          <Group>
            <span>{user?.email}</span>
            <Button variant="subtle" onClick={() => logout()}>
              Log out
            </Button>
          </Group>
        </Group>

        <Group justify="space-between">
          <TextInput
            placeholder="Search by name, email, phone..."
            value={query}
            onChange={(e) => setQuery(e.currentTarget.value)}
            w={320}
          />
          <Button component={Link} to="/customers/new">
            New customer
          </Button>
        </Group>

        {error && <Alert color="red">{error}</Alert>}

        {loading ? (
          <Loader />
        ) : (
          <Table striped highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Customer #</Table.Th>
                <Table.Th>Name</Table.Th>
                <Table.Th>Type</Table.Th>
                <Table.Th>Language</Table.Th>
                <Table.Th>Status</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((c) => (
                <Table.Tr key={c.id} style={{ cursor: 'pointer' }}>
                  <Table.Td ff="monospace">{c.customerNumber}</Table.Td>
                  <Table.Td>
                    <Link to={`/customers/${c.id}`}>
                      {c.customerType === 'business' ? c.companyName : `${c.firstName} ${c.lastName}`}
                    </Link>
                  </Table.Td>
                  <Table.Td>
                    <CustomerTypeBadge type={c.customerType} />
                  </Table.Td>
                  <Table.Td>
                    <LanguageBadge language={c.language} />
                  </Table.Td>
                  <Table.Td>
                    <LifecycleStatusBadge status={c.lifecycleStatus} />
                  </Table.Td>
                </Table.Tr>
              ))}
              {items.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={5}>No customers found.</Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        )}

        <Group>
          <Button
            variant="default"
            disabled={!nextCursor}
            onClick={() => {
              if (nextCursor) load(debouncedQuery, nextCursor)
            }}
          >
            Next page
          </Button>
        </Group>
      </Stack>
    </Container>
  )
}
