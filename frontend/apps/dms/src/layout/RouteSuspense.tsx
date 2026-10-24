import { Component, Suspense, type ErrorInfo, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { Button, Center, Loader, Stack, Text } from '@mantine/core'
import { useTranslation } from 'react-i18next'
import { ChunkLoadError } from './lazyPage'

// KAN-233: the boundary the on-demand screens (lazyPage.ts) render under.

/** The shell's loading state (ProtectedRoute's while the session loads),
 * shown while a screen's chunk is on its way. `fill` takes the viewport
 * outside the shell; inside it the shell stays and the content area waits. */
function RouteLoading({ fill }: { fill: boolean }) {
  return (
    <Center h={fill ? '100vh' : 240}>
      <Loader />
    </Center>
  )
}

function ChunkLoadFailed({ fill }: { fill: boolean }) {
  const { t } = useTranslation()
  return (
    <Center h={fill ? '100vh' : 240} px="md">
      <Stack align="center" gap="sm" maw={420} role="alert">
        <Text fw={600}>{t('common.chunkLoadError.title')}</Text>
        <Text size="sm" ta="center">
          {t('common.chunkLoadError.body')}
        </Text>
        <Button onClick={() => window.location.reload()}>{t('common.chunkLoadError.reload')}</Button>
      </Stack>
    </Center>
  )
}

interface BoundaryProps {
  fill: boolean
  children: ReactNode
}

class ChunkLoadBoundary extends Component<BoundaryProps & { resetKey: string }, { error: unknown; failedAt: string | null }> {
  state = { error: null as unknown, failedAt: null as string | null }

  static getDerivedStateFromError(error: unknown) {
    return { error }
  }

  static getDerivedStateFromProps(props: { resetKey: string }, state: { error: unknown; failedAt: string | null }) {
    // A failure belongs to the screen it happened on: on another path the
    // boundary renders its children again (without remounting them).
    if (state.failedAt !== null && state.failedAt !== props.resetKey) return { error: null, failedAt: null }
    if (state.error !== null && state.failedAt === null) return { failedAt: props.resetKey }
    return null
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    if (error instanceof ChunkLoadError) console.error('A screen failed to load:', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (error === null) return this.props.children
    // Not a chunk failure: rethrown, so it reaches whatever handled it before.
    if (!(error instanceof ChunkLoadError)) throw error
    return <ChunkLoadFailed fill={this.props.fill} />
  }
}

/** The boundary every lazy screen renders under: the loading state while
 * its chunk loads, the translated reload screen if it cannot. */
export function RouteSuspense({ fill, children }: BoundaryProps) {
  const { pathname } = useLocation()
  return (
    <ChunkLoadBoundary resetKey={pathname} fill={fill}>
      <Suspense fallback={<RouteLoading fill={fill} />}>{children}</Suspense>
    </ChunkLoadBoundary>
  )
}
