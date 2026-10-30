import { Avatar } from '@mantine/core'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'

/**
 * KAN-401 — a person's photo, or their initials while they have none. The
 * photo is fetched only for a user whose record says they have one
 * (`hasPhoto`), keyed by the record's version so a new photo replaces the
 * old one; the API serves it `private, no-store`, so it is held here, in
 * memory, never in the browser's cache.
 */
export function UserAvatar({
  photoPath,
  hasPhoto,
  version,
  name,
  size = 'sm',
}: {
  /** `/dealerships/{id}/users/{userId}/photo`, or `/me/photo`. */
  photoPath: string
  hasPhoto: boolean
  version: number
  /** The person's name: the image's alternative text and the initials. */
  name: string
  size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl'
}) {
  const photo = useQuery({
    queryKey: ['user-photo', photoPath, version],
    queryFn: () => api.getBlobUrl(photoPath),
    enabled: hasPhoto,
    staleTime: Infinity,
  })
  const initials = name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]!.toLocaleUpperCase())
    .join('')
  return (
    <Avatar src={hasPhoto ? (photo.data ?? null) : null} alt={name} size={size} radius="xl">
      {initials}
    </Avatar>
  )
}
