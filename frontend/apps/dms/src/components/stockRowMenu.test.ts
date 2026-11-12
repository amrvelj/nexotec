import { describe, expect, it, vi } from 'vitest'
import type { RowMenuAction } from '@nexotec/ui-kit'
import {
  buildStockRowMenu,
  readStockOpenAction,
  stockOpenState,
  type StockRowMenuHandlers,
  type StockRowMenuItem,
  type StockRowMenuPermissions,
} from './stockRowMenu'

const t = (key: string, options?: Record<string, unknown>) => (options?.date ? `${key}:${String(options.date)}` : key)

function handlers(): StockRowMenuHandlers {
  return {
    createContract: vi.fn(),
    createOffer: vi.fn(),
    recordPurchase: vi.fn(),
    stornoPurchase: vi.fn(),
    changeCondition: vi.fn(),
    attachConfiguration: vi.fn(),
    editConfiguration: vi.fn(),
    correctVin: vi.fn(),
  }
}

const ALL: StockRowMenuPermissions = {
  canCreateContract: true,
  canCreateOffer: true,
  canBookPurchase: true,
  canChangeCondition: true,
  canAttachConfiguration: true,
  canEditConfiguration: true,
  canCorrectVin: true,
}
const PIPELINE: StockRowMenuItem = { purchasePrice: null, leftStockAt: null, reservationState: 'none', configurationId: 'cfg-1', vehicleId: null }
const BOUGHT: StockRowMenuItem = { ...PIPELINE, purchasePrice: '20000.00', vehicleId: 'v-1' }

const all = (menu: ReturnType<typeof buildStockRowMenu>) =>
  Object.values(menu.overflow).flatMap((group: RowMenuAction[] | undefined) => group ?? [])
const find = (menu: ReturnType<typeof buildStockRowMenu>, label: string) => all(menu).find((a) => a.label === label)!

describe('buildStockRowMenu (KAN-571; ADR-061, PRD-Stock FR-I-07)', () => {
  it('primary «Vertrag erstellen», alternative «Offerte erstellen»; the overflow carries the full set in order', () => {
    const menu = buildStockRowMenu(t, BOUGHT, handlers(), ALL, null)

    expect(menu.primary?.label).toBe('stockDetail.actions.createContract')
    expect(menu.alternative?.label).toBe('stockDetail.actions.createOffer')
    expect((menu.overflow.edit ?? []).map((a) => a.label)).toEqual([
      'stockDetail.purchase.recordButton',
      'stockDetail.rowMenu.changeCondition',
      'stockDetail.configuration.edit',
      'stockDetail.vinCorrection.action',
    ])
    expect((menu.overflow.createFrom ?? []).map((a) => a.label)).toEqual([
      'stockDetail.actions.createContract',
      'stockDetail.actions.createOffer',
    ])
    expect((menu.overflow.destructive ?? []).map((a) => a.label)).toEqual(['stockDetail.purchase.storno.button'])
  })

  it('both purchase actions are always there; the one that does not apply is disabled with its reason (2a A)', () => {
    const bought = buildStockRowMenu(t, BOUGHT, handlers(), ALL, null)
    expect(find(bought, 'stockDetail.purchase.recordButton')).toMatchObject({
      disabled: true,
      disabledReason: 'stockDetail.rowMenu.purchaseAlreadyBooked',
    })
    expect(find(bought, 'stockDetail.purchase.storno.button').disabled).toBe(false)

    const pipeline = buildStockRowMenu(t, PIPELINE, handlers(), ALL, null)
    expect(find(pipeline, 'stockDetail.purchase.recordButton').disabled).toBe(false)
    expect(find(pipeline, 'stockDetail.purchase.storno.button')).toMatchObject({
      disabled: true,
      disabledReason: 'stockDetail.rowMenu.purchaseNotBooked',
    })
  })

  it('a car that left stock allows no deal, purchase, condition or configuration edit, and says why on each', () => {
    const menu = buildStockRowMenu(t, { ...BOUGHT, leftStockAt: '2026-10-01T00:00:00Z' }, handlers(), ALL, '01.10.2026')

    for (const action of [menu.primary!, menu.alternative!, ...all(menu)]) {
      expect(action.disabled, action.label).toBe(true)
      expect(action.disabledReason, action.label).toBeTruthy()
    }
    expect(find(menu, 'stockDetail.purchase.storno.button').disabledReason).toBe('stockDetail.purchase.storno.leftStock')
    expect(menu.primary?.disabledReason).toBe('stockDetail.actions.leftStock:01.10.2026')
  })

  it('a car a contract reserves keeps «Konfiguration bearbeiten» visible, disabled with the reason (KAN-510)', () => {
    const menu = buildStockRowMenu(t, { ...BOUGHT, reservationState: 'reserved' }, handlers(), ALL, null)
    expect(find(menu, 'stockDetail.configuration.edit')).toMatchObject({
      disabled: true,
      disabledReason: 'stockDetail.configuration.reservedByContract',
    })
  })

  it('a car without a configuration offers «Konfiguration zuordnen» instead (KAN-463)', () => {
    const menu = buildStockRowMenu(t, { ...PIPELINE, configurationId: null }, handlers(), ALL, null)
    const labels = all(menu).map((a) => a.label)
    expect(labels).toContain('stockDetail.configuration.attach')
    expect(labels).not.toContain('stockDetail.configuration.edit')
    // A car that left stock may still be given its configuration: the server
    // allows the attach there (the technical data of a sold car).
    const left = buildStockRowMenu(t, { ...PIPELINE, configurationId: null, leftStockAt: '2026-10-01T00:00:00Z' }, handlers(), ALL, '01.10.2026')
    expect(find(left, 'stockDetail.configuration.attach').disabled).toBeFalsy()
  })

  it('what the role may never use is hidden, not disabled (KAN-430)', () => {
    const menu = buildStockRowMenu(t, BOUGHT, handlers(), {
      canCreateContract: true,
      canCreateOffer: true,
      canBookPurchase: false,
      canChangeCondition: false,
      canAttachConfiguration: false,
      canEditConfiguration: false,
      canCorrectVin: false,
    }, null)
    expect(all(menu).map((a) => a.label)).toEqual(['stockDetail.actions.createContract', 'stockDetail.actions.createOffer'])
    expect(Object.keys(menu.offered).sort()).toEqual(['createContract', 'createOffer'])
  })

  it('a role that may not create offers or contracts gets neither header button nor menu entry (sales_offers, sales_contracts)', () => {
    const menu = buildStockRowMenu(t, BOUGHT, handlers(), { ...ALL, canCreateContract: false, canCreateOffer: false }, null)
    expect(menu.primary).toBeUndefined()
    expect(menu.alternative).toBeUndefined()
    expect(menu.overflow.createFrom).toEqual([])
    expect(menu.offered.createContract).toBeUndefined()
    expect(menu.offered.createOffer).toBeUndefined()
  })

  it('while a deal is being created, both deal actions wait for it and say so', () => {
    const menu = buildStockRowMenu(t, BOUGHT, handlers(), ALL, null, true)
    for (const action of [menu.primary!, menu.alternative!]) {
      expect(action).toMatchObject({ disabled: true, disabledReason: 'stockDetail.rowMenu.creatingDeal' })
    }
  })

  it('each item calls its own handler', () => {
    const h = handlers()
    const menu = buildStockRowMenu(t, BOUGHT, h, ALL, null)
    find(menu, 'stockDetail.rowMenu.changeCondition').onClick()
    find(menu, 'stockDetail.purchase.storno.button').onClick()
    menu.primary!.onClick()
    expect(h.changeCondition).toHaveBeenCalledOnce()
    expect(h.stornoPurchase).toHaveBeenCalledOnce()
    expect(h.createContract).toHaveBeenCalledOnce()
  })

  it('«VIN korrigieren» (KAN-557): offered on a bought car, disabled on one that left stock or has no vehicle-mdm record', () => {
    const h = handlers()
    const bought = buildStockRowMenu(t, BOUGHT, h, ALL, null)
    const entry = find(bought, 'stockDetail.vinCorrection.action')
    expect(entry.disabled).toBe(false)
    entry.onClick()
    expect(h.correctVin).toHaveBeenCalledOnce()
    expect(bought.offered.correctVin).toBe(entry)

    const left = buildStockRowMenu(t, { ...BOUGHT, leftStockAt: '2026-10-01T00:00:00Z' }, handlers(), ALL, '01.10.2026')
    expect(find(left, 'stockDetail.vinCorrection.action')).toMatchObject({
      disabled: true,
      disabledReason: 'stockDetail.actions.leftStock:01.10.2026',
    })
    const pipeline = buildStockRowMenu(t, PIPELINE, handlers(), ALL, null)
    expect(find(pipeline, 'stockDetail.vinCorrection.action')).toMatchObject({
      disabled: true,
      disabledReason: 'stockDetail.vinCorrection.needsPurchase',
    })
    const hidden = buildStockRowMenu(t, BOUGHT, handlers(), { ...ALL, canCorrectVin: false }, null)
    expect(all(hidden).some((a) => a.label === 'stockDetail.vinCorrection.action')).toBe(false)
    expect(hidden.offered.correctVin).toBeUndefined()
  })

  it('the router state round-trips, and anything else reads as no action', () => {
    expect(readStockOpenAction(stockOpenState('changeCondition'))).toBe('changeCondition')
    expect(readStockOpenAction(stockOpenState('correctVin'))).toBe('correctVin')
    expect(readStockOpenAction({ stockOpenAction: 'deleteEverything' })).toBeNull()
    expect(readStockOpenAction(null)).toBeNull()
  })
})
