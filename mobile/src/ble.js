// 블루투스 혈압계 · 체중계 (표준 BLE 규격만)
//   혈압: Blood Pressure 서비스 0x1810 / 측정값 0x2A35
//   체중: Weight Scale 서비스 0x181D / 측정값 0x2A9D
// react-native-ble-plx 가 필요하고, Expo Go 가 아니라 개발 빌드(npx expo run:android)에서만 동작한다.
import { BleManager } from "react-native-ble-plx";
import { decodeBase64, parseBloodPressure, parseWeight } from "./bleParse";

const BP_SERVICE = "00001810-0000-1000-8000-00805f9b34fb";
const BP_MEASURE = "00002a35-0000-1000-8000-00805f9b34fb";
const WS_SERVICE = "0000181d-0000-1000-8000-00805f9b34fb";
const WS_MEASURE = "00002a9d-0000-1000-8000-00805f9b34fb";

const manager = new BleManager();

// 기기를 찾아 연결하고, 측정값이 한 번 오면 돌려준다. kind: "bp" | "weight"
export default function readOnce(kind, { timeoutMs = 60000, onStatus = () => {} } = {}) {
  const service = kind === "bp" ? BP_SERVICE : WS_SERVICE;
  const characteristic = kind === "bp" ? BP_MEASURE : WS_MEASURE;
  return new Promise((resolve, reject) => {
    let done = false, device = null;
    const finish = (err, value) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      manager.stopDeviceScan();
      if (device) device.cancelConnection().catch(() => {});
      err ? reject(err) : resolve(value);
    };
    const timer = setTimeout(() => finish(new Error("기기를 찾지 못했어요. 기기에서 측정을 시작해 주세요.")), timeoutMs);
    onStatus("기기를 찾는 중… 혈압계(체중계)에서 측정을 시작하세요");
    manager.startDeviceScan([service], null, async (error, found) => {
      if (error) return finish(error);
      if (!found || device) return;
      device = found;
      manager.stopDeviceScan();
      try {
        onStatus(`${found.name || "기기"} 연결 중…`);
        await device.connect();
        await device.discoverAllServicesAndCharacteristics();
        onStatus("측정값을 기다리는 중…");
        device.monitorCharacteristicForService(service, characteristic, (err, c) => {
          if (err) return finish(err);
          const bytes = decodeBase64(c.value);
          finish(null, kind === "bp" ? parseBloodPressure(bytes) : parseWeight(bytes));
        });
      } catch (e) { finish(e); }
    });
  });
}
