import type { ReactNode } from 'react'
import { Center, Paper, Stack, Text, Title } from '@mantine/core'

/** The layout every unauthenticated identity screen shares (KAN-196): one
 * card, centred, the product name above it. Layout only — the components
 * inside are Mantine's, as everywhere else. */
export function AuthScreen({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return (
    <Center mih="100vh" p="md">
      <Stack w="100%" maw={420} gap="md">
        <Text ta="center" fw={600} fz="lg">
          Nexotec
        </Text>
        <Paper withBorder radius="md" p="xl" shadow="xs">
          <Stack gap="md">
            <div>
              <Title order={2} fz="h3">
                {title}
              </Title>
              {hint && (
                <Text c="dimmed" fz="sm" mt={4}>
                  {hint}
                </Text>
              )}
            </div>
            {children}
          </Stack>
        </Paper>
      </Stack>
    </Center>
  )
}
