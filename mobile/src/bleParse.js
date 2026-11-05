// 블루투스 측정값(바이트)을 숫자로 바꾸는 함수들. 기기 없이도 시험할 수 있도록 따로 둔다.

const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
export function decodeBase64(s) {
  const clean = (s || "").replace(/=+$/, "");
  const out = [];
  let buf = 0, bits = 0;
  for (const ch of clean) {
    buf = (buf << 6) | B64.indexOf(ch);
    bits += 6;
    if (bits >= 8) { bits -= 8; out.push((buf >> bits) & 0xff); }
  }
  return out;
}

// IEEE 11073 SFLOAT(16비트): 위 4비트 = 지수(부호 있음), 아래 12비트 = 가수(부호 있음)
export function sfloat(lo, hi) {
  const raw = lo | (hi << 8);
  if ([0x07ff, 0x0800, 0x07fe, 0x0802, 0x0801].includes(raw)) return null;   // NaN · 무한대 등
  let mantissa = raw & 0x0fff;
  let exponent = raw >> 12;
  if (mantissa >= 0x0800) mantissa -= 0x1000;
  if (exponent >= 0x8) exponent -= 0x10;
  return mantissa * Math.pow(10, exponent);
}

// 0x2A35 Blood Pressure Measurement
export function parseBloodPressure(b) {
  const flags = b[0];
  const kpa = flags & 0x01;
  let sys = sfloat(b[1], b[2]), dia = sfloat(b[3], b[4]);
  if (kpa) { sys = sys * 7.50062; dia = dia * 7.50062; }       // kPa → mmHg
  let i = 7;
  if (flags & 0x02) i += 7;                                     // 시각 정보 건너뛰기
  const pulse = flags & 0x04 ? sfloat(b[i], b[i + 1]) : null;
  return { systolic_bp: Math.round(sys), diastolic_bp: Math.round(dia), pulse: pulse && Math.round(pulse) };
}

// 0x2A9D Weight Measurement
export function parseWeight(b) {
  const flags = b[0];
  const raw = b[1] | (b[2] << 8);
  const kg = flags & 0x01 ? raw * 0.01 * 0.45359237 : raw * 0.005;   // 파운드(0.01 lb) 또는 kg(0.005 kg)
  return { weight_kg: Math.round(kg * 10) / 10 };
}
