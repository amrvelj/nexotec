import { useEffect, useRef, useState } from 'react'
import { Combobox, Group, Loader, Text, TextInput, useCombobox, type MantineSize } from '@mantine/core'
import { useDebouncedValue } from '@mantine/hooks'
import { Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import googleMapsLogo from '../assets/google-maps-logo-gray.svg'
import { useAddressAutocompleteConfig } from '../hooks/useAddressAutocompleteConfig'
import { useFieldHint } from '../hooks/useFieldHint'
import { resolveLanguage } from '../hooks/useReferenceValueOptions'
import {
  fetchAddressSuggestions,
  newSessionToken,
  resolvePlaceAddress,
  type AddressSuggestion,
  type ResolvedAddress,
} from '../utils/googlePlaces'

// Three characters before the first request, a short pause between them: a
// keystroke run is one billing session either way, this only spares the
// obvious no-hit requests.
const MIN_INPUT_LENGTH = 3
const DEBOUNCE_MS = 250

export interface AddressSearchFieldProps {
  /** The form's current country (alpha-2): suggestions are restricted to it. */
  country: string
  /** The picked address — the caller writes it into its own fields, all of which stay editable. */
  onSelect: (address: ResolvedAddress) => void
  id?: string
  size?: MantineSize
}

/**
 * KAN-37 (ADR-101): the one search field above an address block. Typing
 * shows Google Places suggestions; picking one hands the caller street,
 * house number, postal code, locality and country. Renders nothing while
 * the Betrieb's switch is off or the platform holds no key — the address
 * block is then exactly what it was before this field existed. Suggestions
 * live in this component's state only (never stored, never logged — see
 * utils/googlePlaces.ts); the Google logo in the dropdown is the
 * attribution the Places API policies require when results are shown
 * without a map.
 */
export function AddressSearchField({ country, onSelect, id, size }: AddressSearchFieldProps) {
  const { enabled, browserKey } = useAddressAutocompleteConfig()
  if (!enabled || !browserKey) return null
  return <SearchField browserKey={browserKey} country={country} onSelect={onSelect} id={id} size={size} />
}

function SearchField({ browserKey, country, onSelect, id, size }: AddressSearchFieldProps & { browserKey: string }) {
  const { t, i18n } = useTranslation()
  const hint = useFieldHint()
  const language = resolveLanguage(i18n.language)
  const combobox = useCombobox({ onDropdownClose: () => combobox.resetSelectedOption() })
  const [query, setQuery] = useState('')
  const [debouncedQuery] = useDebouncedValue(query, DEBOUNCE_MS)
  const [suggestions, setSuggestions] = useState<AddressSuggestion[]>([])
  const [searching, setSearching] = useState(false)
  // `null` until the first keystroke of a session; a pick ends the session.
  const sessionToken = useRef<string | null>(null)
  // The text of the last pick: typing again starts a new search, re-picking
  // the same text does not.
  const picked = useRef<string | null>(null)
  const inFlight = useRef<AbortController | null>(null)

  useEffect(() => {
    inFlight.current?.abort()
    const input = debouncedQuery.trim()
    if (input.length < MIN_INPUT_LENGTH || input === picked.current) {
      setSuggestions([])
      setSearching(false)
      return
    }
    const controller = new AbortController()
    inFlight.current = controller
    sessionToken.current ??= newSessionToken()
    setSearching(true)
    fetchAddressSuggestions(input, country || 'CH', {
      browserKey,
      sessionToken: sessionToken.current,
      language,
      signal: controller.signal,
    })
      .then((found) => {
        if (controller.signal.aborted) return
        setSuggestions(found)
        combobox.openDropdown()
      })
      .catch(() => {
        // A blocked or failed request: no suggestions, nothing said — the
        // fields are plain inputs. An abort lands here too, by design.
        if (!controller.signal.aborted) setSuggestions([])
      })
      .finally(() => {
        if (!controller.signal.aborted) setSearching(false)
      })
    return () => controller.abort()
    // `combobox` is a stable store; `country` and `language` are inputs of the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedQuery, country, language, browserKey])

  const pick = async (placeId: string) => {
    const suggestion = suggestions.find((s) => s.placeId === placeId)
    if (!suggestion) return
    combobox.closeDropdown()
    const token = sessionToken.current ?? newSessionToken()
    // The session ends with the pick, whatever the details call answers.
    sessionToken.current = null
    setSearching(true)
    try {
      const address = await resolvePlaceAddress(placeId, { browserKey, sessionToken: token, language })
      if (address) {
        picked.current = suggestion.text
        setQuery(suggestion.text)
        setSuggestions([])
        onSelect(address)
      }
    } catch {
      // Same as above: a failed details call leaves the fields as they were.
    } finally {
      setSearching(false)
    }
  }

  const label = t('addressSearch.label')
  return (
    <Combobox store={combobox} onOptionSubmit={(value) => void pick(value)} withinPortal>
      <Combobox.Target>
        <TextInput
          id={id}
          size={size}
          {...hint(label, t('addressSearch.hint'))}
          placeholder={t('addressSearch.placeholder')}
          value={query}
          autoComplete="off"
          onChange={(event) => {
            setQuery(event.currentTarget.value)
            combobox.updateSelectedOptionIndex()
          }}
          onFocus={() => suggestions.length > 0 && combobox.openDropdown()}
          onBlur={() => combobox.closeDropdown()}
          rightSection={searching ? <Loader size="xs" /> : <Search size={16} aria-hidden="true" />}
        />
      </Combobox.Target>
      <Combobox.Dropdown hidden={suggestions.length === 0 && !combobox.dropdownOpened}>
        <Combobox.Options>
          {suggestions.map((suggestion) => (
            <Combobox.Option value={suggestion.placeId} key={suggestion.placeId}>
              <Text size="sm">{suggestion.mainText}</Text>
              {suggestion.secondaryText && (
                <Text size="xs" c="dimmed">
                  {suggestion.secondaryText}
                </Text>
              )}
            </Combobox.Option>
          ))}
          {suggestions.length === 0 && <Combobox.Empty>{t('addressSearch.noResults')}</Combobox.Empty>}
        </Combobox.Options>
        <Combobox.Footer>
          <Group justify="flex-end">
            <img src={googleMapsLogo} alt={t('addressSearch.attribution')} height={12} />
          </Group>
        </Combobox.Footer>
      </Combobox.Dropdown>
    </Combobox>
  )
}
