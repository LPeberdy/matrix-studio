#include "board_config.h"

namespace matrix_studio {
namespace board {

const char* shift_driver_name() {
  switch (kShiftDriver) {
    case Hub75ShiftDriver::GENERIC: return "GENERIC";
    case Hub75ShiftDriver::FM6126A: return "FM6126A";
    case Hub75ShiftDriver::ICN2038S: return "ICN2038S";
    case Hub75ShiftDriver::FM6124: return "FM6124";
    case Hub75ShiftDriver::MBI5124: return "MBI5124";
    case Hub75ShiftDriver::DP3246: return "DP3246";
  }
  return "unknown";
}

const char* scan_wiring_name() {
  switch (kScanWiring) {
    case Hub75ScanWiring::STANDARD_TWO_SCAN: return "STANDARD_TWO_SCAN";
    case Hub75ScanWiring::SCAN_1_4_16PX_HIGH: return "SCAN_1_4_16PX_HIGH";
    case Hub75ScanWiring::SCAN_1_8_32PX_HIGH: return "SCAN_1_8_32PX_HIGH";
    case Hub75ScanWiring::SCAN_1_8_32PX_FULL: return "SCAN_1_8_32PX_FULL";
    case Hub75ScanWiring::SCAN_1_8_40PX_HIGH: return "SCAN_1_8_40PX_HIGH";
    case Hub75ScanWiring::SCAN_1_8_64PX_HIGH: return "SCAN_1_8_64PX_HIGH";
  }
  return "unknown";
}

}  // namespace board
}  // namespace matrix_studio
