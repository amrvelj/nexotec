import type { CustomerVehicleRead } from '../../api/types'

// KAN-31: falls back to the vehicle number when the vehicle has no
// catalogue match — make/model/modelYear are null in that case (the
// common one today, not an edge case; see VehiclePartySummary's own
// doc). Never renders "null null (null)". KAN-339: a party whose vehicle
// is not in vehicle-mdm has no stored label at all, so the last fallback
// says so (`notInRegister`) rather than rendering an empty cell.
export function vehicleLabel(vehicle: CustomerVehicleRead['vehicle'], notInRegister: string): string {
  if (vehicle.make && vehicle.model) {
    return vehicle.modelYear ? `${vehicle.make} ${vehicle.model} (${vehicle.modelYear})` : `${vehicle.make} ${vehicle.model}`
  }
  return vehicle.vehicleNumber ?? notInRegister
}
