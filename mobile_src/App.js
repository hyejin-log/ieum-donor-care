// 이음 공여자 앱 (React Native · Expo)
// 화면: 로그인 → 홈 → 오늘 기록 / 이음 안내(채팅) / 설정
// 어르신도 쓰기 쉽도록 글자를 크게, 버튼을 크게 만든다.
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator, Alert, KeyboardAvoidingView, Platform, Pressable, RefreshControl,
  SafeAreaView, ScrollView, StatusBar, StyleSheet, Text, TextInput, View,
} from "react-native";
import * as SecureStore from "expo-secure-store";
import { api, AuthError, getServer, loadConfig, login, logout, setServer } from "./src/api";
import { cancelDaily, scheduleDaily } from "./src/reminders";
import readBle from "./src/bleBridge";

const C = { bg: "#EDF1F6", card: "#FFFFFF", soft: "#F4F7FB", navy: "#17233D", ink: "#22304A", muted: "#6B778C",
            blue: "#3F6FE0", blueSoft: "#E3EBFC", ok: "#2E9E7A", okSoft: "#E1F4EE", amber: "#A86E0C",
            amberSoft: "#FBF1DD", warn: "#C2462B", warnSoft: "#FCE9E4" };
const UP_KO = { Negative: "음성", Trace: "미량", "1+": "1+", "2+": "2+", "3+": "3+" };
const STATUS_KO = { done: "완료", missed: "놓침", window: "검사 기간", upcoming: "예정" };
const todayStr = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const dot = (d) => (d ? d.replaceAll("-", ".") : "");
const daysUntil = (d) => Math.round((new Date(d) - new Date(todayStr())) / 86400000);

// ---------- 판정 (웹 화면 · 서버와 같은 기준) ----------
function bpLevel(s, d) {
  if (s >= 180 || d >= 110) return ["urgent", "매우 높음"];
  if (s >= 135 || d >= 85) return ["warn", "기준보다 높음"];
  if (s < 90 || d < 60) return ["warn", "낮음"];
  return ["ok", "정상 범위"];
}
const egfrLevel = (v) => (v == null ? null : v >= 60 ? ["ok", v >= 90 ? "정상" : "기증 후 흔함"] : v >= 45 ? ["warn", "확인 필요"] : ["urgent", "상담 필요"]);
const crLevel = (v, sex) => (v == null ? null : v > (sex === "F" ? 1.1 : 1.3) ? ["warn", "범위보다 높음"] : ["ok", "범위 안"]);
const upLevel = (v) => (v == null ? null : v === "Negative" ? ["ok", "정상"] : v === "Trace" || v === "1+" ? ["warn", v === "Trace" ? "다시 확인" : "양성"] : ["urgent", "양성 · 확인"]);

function Chip({ level }) {
  if (!level) return null;
  const [k, text] = level;
  const bg = k === "ok" ? C.okSoft : k === "warn" ? C.amberSoft : C.warnSoft;
  const fg = k === "ok" ? C.ok : k === "warn" ? C.amber : C.warn;
  return <Text style={[s.chip, { backgroundColor: bg, color: fg }]}>{text}</Text>;
}
const Card = ({ children, style }) => <View style={[s.card, style]}>{children}</View>;
const Btn = ({ title, onPress, kind = "primary", disabled }) => (
  <Pressable onPress={onPress} disabled={disabled}
    style={({ pressed }) => [s.btn, kind === "ghost" && s.btnGhost, (pressed || disabled) && { opacity: 0.6 }]}>
    <Text style={[s.btnText, kind === "ghost" && { color: C.blue }]}>{title}</Text>
  </Pressable>
);

export default function App() {
  const [screen, setScreen] = useState("loading");
  const [me, setMe] = useState(null);

  const goLogin = () => { setMe(null); setScreen("login"); };
  const handle = (e) => { if (e instanceof AuthError) goLogin(); else Alert.alert("오류", e.message || "서버에 연결할 수 없어요"); };

  useEffect(() => {
    (async () => {
      const cfg = await loadConfig();
      if (!cfg.base || !cfg.loggedIn) return setScreen("login");
      try { setMe(await api("/auth/me")); setScreen("home"); } catch { setScreen("login"); }
    })();
  }, []);

  if (screen === "loading") return <Center><ActivityIndicator size="large" /></Center>;
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: C.bg }}>
      <StatusBar barStyle="dark-content" />
      {screen === "login" && <Login onDone={(u) => { setMe(u); setScreen("home"); }} />}
      {screen === "home" && me && <Home me={me} go={setScreen} onError={handle} />}
      {screen === "record" && me && <Record me={me} back={() => setScreen("home")} onError={handle} />}
      {screen === "chat" && me && <Chat me={me} back={() => setScreen("home")} onError={handle} />}
      {screen === "settings" && <Settings back={() => setScreen("home")} onLogout={async () => { await logout(); goLogin(); }} />}
    </SafeAreaView>
  );
}

const Center = ({ children }) => <View style={{ flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: C.bg }}>{children}</View>;

// ---------- 로그인 ----------
function Login({ onDone }) {
  const [server, setSrv] = useState(getServer() || "http://192.168.0.10:8000");
  const [id, setId] = useState("");
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const submit = async () => {
    setBusy(true); setMsg("");
    try {
      await setServer(server);
      await login(id.trim(), pw);
      const u = await api("/auth/me");
      if (u.role !== "donor") { setMsg("의료진은 웹 화면(/staff)을 이용해 주세요."); await logout(); return; }
      onDone(u);
    } catch (e) { setMsg(e.message || "서버에 연결할 수 없어요"); } finally { setBusy(false); }
  };
  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={s.page}>
        <Text style={s.logo}>이음</Text>
        <Text style={s.sub}>생체 신장 기증 이후의 건강을 끊기지 않게 잇습니다</Text>
        <Card>
          <Text style={s.label}>서버 주소</Text>
          <TextInput style={s.input} value={server} onChangeText={setSrv} autoCapitalize="none" keyboardType="url" />
          <Text style={s.help}>PC에서 서버를 켜고, 같은 와이파이의 PC 주소를 넣으세요.</Text>
          <Text style={s.label}>아이디</Text>
          <TextInput style={s.input} value={id} onChangeText={setId} autoCapitalize="none" />
          <Text style={s.label}>비밀번호</Text>
          <TextInput style={s.input} value={pw} onChangeText={setPw} secureTextEntry />
          <Btn title={busy ? "로그인 중…" : "로그인"} onPress={submit} disabled={busy} />
          {!!msg && <Text style={s.err}>{msg}</Text>}
          <Text style={s.help}>회원가입은 웹 화면(/login)에서 공여자 코드와 출생 연도로 할 수 있어요.</Text>
        </Card>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

// ---------- 홈 ----------
function Home({ me, go, onError }) {
  const [d, setD] = useState(null);
  const [loading, setLoading] = useState(false);
  const code = encodeURIComponent(me.donor_code);
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [summary, home, labs, notis] = await Promise.all([
        api(`/donors/${code}/summary`), api(`/donors/${code}/home`), api(`/donors/${code}/labs`), api(`/donors/${code}/notifications`)]);
      setD({ summary, home, labs, notis });
    } catch (e) { onError(e); } finally { setLoading(false); }
  }, [code]);
  useEffect(() => { load(); }, [load]);
  if (!d) return <Center><ActivityIndicator size="large" /></Center>;

  const { summary: sm, home, labs, notis } = d;
  const doneToday = home.some((r) => r.measured_at === todayStr());
  const lastBp = [...home].reverse().find((r) => r.systolic_bp != null);
  const next = sm.visits.find((v) => v.status === "missed") || sm.visits.find((v) => v.status === "window") || sm.visits.find((v) => v.status === "upcoming");
  const lab = labs[labs.length - 1];
  const crs = labs.filter((l) => l.creatinine != null).slice(-6);
  const top = notis.find((n) => n.level === "urgent") || notis.find((n) => n.level === "warn");

  return (
    <ScrollView contentContainerStyle={s.page} refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}>
      <View style={s.row}>
        <Text style={s.logo}>이음</Text>
        <Pressable onPress={() => go("settings")}><Text style={s.link}>설정</Text></Pressable>
      </View>
      <Text style={s.hello}>{me.display_name}님, 기증 후 <Text style={{ color: C.blue }}>{sm.days_since_donation}일째</Text>예요</Text>

      {top && (
        <View style={[s.banner, { backgroundColor: top.level === "urgent" ? C.warnSoft : C.amberSoft }]}>
          <Text style={[s.bannerTitle, { color: top.level === "urgent" ? C.warn : C.amber }]}>{top.title}</Text>
          <Text style={s.body}>{top.body}</Text>
        </View>
      )}

      <Card style={{ backgroundColor: C.blue }}>
        <Text style={[s.label, { color: "#DCE6FB" }]}>오늘의 혈압 · 체중</Text>
        <Text style={[s.big, { color: "#fff" }]}>{doneToday ? "오늘 기록 완료 ✓" : "아직 기록 전이에요"}</Text>
        <Text style={[s.body, { color: "#EAF0FD" }]}>매일 아침 같은 시간에 재서 적어 주세요.</Text>
        <Btn title={doneToday ? "다시 기록하기" : "기록하기"} kind="ghost" onPress={() => go("record")} />
      </Card>

      <Card>
        <Text style={s.label}>다음 추적검사</Text>
        {next ? (<>
          <Text style={s.big}>{next.status === "missed" ? "검사를 놓쳤어요" : next.status === "window" ? "지금 검사 기간" : `D-${daysUntil(next.due_date)}`}</Text>
          <Text style={s.body}>{next.visit_name} · 예정일 {dot(next.due_date)}</Text>
        </>) : <Text style={s.body}>등록된 일정이 없어요</Text>}
        <View style={[s.row, { marginTop: 10, flexWrap: "wrap", gap: 6 }]}>
          {sm.visits.map((v) => <Text key={v.visit_name} style={[s.step, v.status === "done" && { backgroundColor: C.okSoft, color: C.ok }, v.status === "missed" && { backgroundColor: C.warnSoft, color: C.warn }]}>{v.visit_name.replace("기증 후 ", "")} {STATUS_KO[v.status]}</Text>)}
        </View>
      </Card>

      <Card>
        <Text style={s.label}>내 혈압</Text>
        {lastBp ? (<>
          <Text style={s.big}>{lastBp.systolic_bp}/{lastBp.diastolic_bp} <Text style={s.unit}>mmHg</Text></Text>
          <Chip level={bpLevel(lastBp.systolic_bp, lastBp.diastolic_bp)} />
          <Text style={s.help}>앞 숫자는 심장이 피를 내보낼 때, 뒤 숫자는 쉴 때의 혈압이에요. 집에서 잰 혈압은 135/85 미만이 기준이에요.</Text>
        </>) : <Text style={s.body}>아직 기록이 없어요</Text>}
      </Card>

      <Card>
        <Text style={s.label}>병원 검사 결과 {lab ? `· ${dot(lab.tested_at)}` : ""}</Text>
        <View style={[s.row, { gap: 8, marginTop: 6 }]}>
          <Tile name="eGFR" plain="콩팥 기능 점수" value={lab?.egfr} level={egfrLevel(lab?.egfr)} />
          <Tile name="크레아티닌" plain="콩팥이 거르는 노폐물" value={lab?.creatinine} level={crLevel(lab?.creatinine, sm.sex)} />
          <Tile name="소변 단백" plain="소변에 새는 단백질" value={lab?.urine_protein ? UP_KO[lab.urine_protein] : null} level={upLevel(lab?.urine_protein)} />
        </View>
        {crs.length > 0 && (<>
          <Text style={[s.label, { marginTop: 14 }]}>크레아티닌 변화 (mg/dL)</Text>
          <View style={s.bars}>
            {crs.map((l) => (
              <View key={l.tested_at} style={{ flex: 1, alignItems: "center" }}>
                <Text style={s.barVal}>{l.creatinine}</Text>
                <View style={[s.bar, { height: Math.max(8, Number(l.creatinine) / 2 * 90), backgroundColor: Number(l.creatinine) > (sm.sex === "F" ? 1.1 : 1.3) ? C.amber : C.blue }]} />
                <Text style={s.barCap}>{l.tested_at.slice(2, 7).replace("-", ".")}</Text>
              </View>
            ))}
          </View>
        </>)}
        <Text style={s.help}>기준은 일반 성인 참고 범위예요. 수치의 의미는 담당 의료진과 상담하세요.</Text>
      </Card>

      <Btn title="이음 안내에게 물어보기" onPress={() => go("chat")} />
    </ScrollView>
  );
}

const Tile = ({ name, plain, value, level }) => (
  <View style={s.tile}>
    <Text style={s.tileName}>{name}</Text>
    <Text style={s.tilePlain}>{plain}</Text>
    <Text style={s.tileVal}>{value ?? "-"}</Text>
    <Chip level={level} />
  </View>
);

// ---------- 오늘 기록 ----------
function Record({ me, back, onError }) {
  const [sys, setSys] = useState("");
  const [dia, setDia] = useState("");
  const [wt, setWt] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [bleMsg, setBleMsg] = useState("");

  const fromBle = async (kind) => {
    try {
      const v = await readBle(kind, { onStatus: setBleMsg });
      if (kind === "bp") { setSys(String(v.systolic_bp)); setDia(String(v.diastolic_bp)); }
      else setWt(String(v.weight_kg));
      setBleMsg("받았어요. 숫자를 확인하고 저장하세요.");
    } catch (e) { setBleMsg(e.message || "블루투스 연결에 실패했어요"); }
  };
  const save = async () => {
    setBusy(true);
    try {
      const r = await api("/home-measurements", { method: "POST", body: {
        donor_code: me.donor_code, measured_at: todayStr(),
        systolic_bp: Number(sys) || null, diastolic_bp: Number(dia) || null, weight_kg: wt ? Number(wt) : null } });
      setResult(r.bp);
    } catch (e) { onError(e); } finally { setBusy(false); }
  };

  if (result) {
    const bg = result.level === "normal" ? C.okSoft : result.level === "crisis" ? C.warnSoft : C.amberSoft;
    return (
      <ScrollView contentContainerStyle={s.page}>
        <Card style={{ backgroundColor: bg }}>
          <Text style={s.big}>{sys}/{dia} mmHg</Text>
          <Text style={s.bannerTitle}>{result.title}</Text>
          <Text style={s.body}>{result.advice}</Text>
        </Card>
        <Btn title="확인" onPress={back} />
      </ScrollView>
    );
  }
  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
      <ScrollView contentContainerStyle={s.page}>
        <Pressable onPress={back}><Text style={s.link}>← 돌아가기</Text></Pressable>
        <Text style={s.title}>오늘의 혈압 · 체중</Text>
        <Card style={{ backgroundColor: C.soft }}>
          <Text style={s.bannerTitle}>언제, 어떻게 재나요?</Text>
          {["아침에 일어나서 1시간 안에, 소변을 본 뒤", "약 먹기 전 · 아침 식사 전", "등을 기대고 앉아 5분 쉰 뒤 팔을 심장 높이에",
            "1~2분 간격으로 2번 재서 평균을 적기", "체중은 소변 본 뒤 식사 전, 가벼운 옷으로"].map((t) => <Text key={t} style={s.body}>• {t}</Text>)}
        </Card>
        {readBle && (
          <View style={[s.row, { gap: 8 }]}>
            <View style={{ flex: 1 }}><Btn title="혈압계에서 받기" kind="ghost" onPress={() => fromBle("bp")} /></View>
            <View style={{ flex: 1 }}><Btn title="체중계에서 받기" kind="ghost" onPress={() => fromBle("weight")} /></View>
          </View>
        )}
        {!!bleMsg && <Text style={s.help}>{bleMsg}</Text>}
        <Text style={s.label}>앞 숫자 (수축기)</Text>
        <TextInput style={s.inputBig} value={sys} onChangeText={setSys} keyboardType="number-pad" placeholder="예: 120" />
        <Text style={s.label}>뒤 숫자 (이완기)</Text>
        <TextInput style={s.inputBig} value={dia} onChangeText={setDia} keyboardType="number-pad" placeholder="예: 80" />
        <Text style={s.label}>체중 (kg, 선택)</Text>
        <TextInput style={s.inputBig} value={wt} onChangeText={setWt} keyboardType="decimal-pad" placeholder="예: 60.5" />
        <Btn title={busy ? "저장 중…" : "저장"} onPress={save} disabled={busy || !sys || !dia} />
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

// ---------- 이음 안내 (채팅) ----------
const SUGGEST = ["다음 검사 언제예요?", "최근 혈압 어때요?", "혈압은 어떻게 재요?", "eGFR이 뭐예요?", "크레아티닌이 뭐예요?", "진통제 먹어도 돼요?"];
function Chat({ me, back, onError }) {
  const [msgs, setMsgs] = useState([{ role: "assistant", content: `안녕하세요, ${me.display_name}님. 아래 질문을 누르거나 궁금한 점을 적어 주세요.` }]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const ask = async (text) => {
    if (!text.trim() || busy) return;
    const next = [...msgs, { role: "user", content: text.trim() }];
    setMsgs(next); setQ(""); setBusy(true);
    try {
      const r = await api(`/donors/${encodeURIComponent(me.donor_code)}/chat`, { method: "POST",
        body: { messages: next.filter((m, i) => i > 0).slice(-10) } });
      setMsgs([...next, { role: "assistant", content: r.reply, mode: r.mode }]);
    } catch (e) { onError(e); setMsgs(next); } finally { setBusy(false); }
  };
  return (
    <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
      <View style={[s.row, { padding: 16 }]}>
        <Pressable onPress={back}><Text style={s.link}>← 홈</Text></Pressable>
        <Text style={s.title}>이음 안내</Text>
        <View style={{ width: 40 }} />
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ flexGrow: 0 }} contentContainerStyle={{ paddingHorizontal: 16, gap: 6 }}>
        {SUGGEST.map((t) => <Pressable key={t} onPress={() => ask(t)}><Text style={s.suggest}>{t}</Text></Pressable>)}
      </ScrollView>
      <ScrollView contentContainerStyle={{ padding: 16, gap: 8 }}>
        {msgs.map((m, i) => (
          <View key={i} style={[s.msg, m.role === "user" ? s.msgUser : s.msgBot, m.mode === "safety" && { backgroundColor: C.warnSoft }]}>
            <Text style={[s.body, m.role === "user" && { color: "#fff" }]}>{m.content.replace(/\*\*/g, "")}</Text>
          </View>
        ))}
        {busy && <ActivityIndicator />}
      </ScrollView>
      <View style={[s.row, { padding: 12, gap: 8 }]}>
        <TextInput style={[s.input, { flex: 1, marginBottom: 0 }]} value={q} onChangeText={setQ} placeholder="궁금한 점을 입력하세요" onSubmitEditing={() => ask(q)} />
        <Pressable onPress={() => ask(q)} style={s.send}><Text style={{ color: "#fff", fontWeight: "700" }}>보내기</Text></Pressable>
      </View>
      <Text style={[s.help, { textAlign: "center", paddingBottom: 8 }]}>참고용 안내이며 진료를 대신하지 않아요. 응급 상황에는 119.</Text>
    </KeyboardAvoidingView>
  );
}

// ---------- 설정: 매일 알림 시각 ----------
function Settings({ back, onLogout }) {
  const [hour, setHour] = useState(8);
  const [minute, setMinute] = useState(0);
  const [on, setOn] = useState(false);
  useEffect(() => { (async () => {
    const saved = await SecureStore.getItemAsync("reminder");
    if (saved) { const r = JSON.parse(saved); setHour(r.hour); setMinute(r.minute); setOn(r.on); }
  })(); }, []);
  const apply = async (next) => {
    if (next.on) {
      const ok = await scheduleDaily(next.hour, next.minute);
      if (!ok) { Alert.alert("알림 권한이 필요해요", "휴대폰 설정에서 이음 알림을 허용해 주세요."); return; }
    } else await cancelDaily();
    setHour(next.hour); setMinute(next.minute); setOn(next.on);
    await SecureStore.setItemAsync("reminder", JSON.stringify(next));
  };
  const step = (dm) => { const t = (hour * 60 + minute + dm + 1440) % 1440; apply({ hour: Math.floor(t / 60), minute: t % 60, on }); };
  return (
    <ScrollView contentContainerStyle={s.page}>
      <Pressable onPress={back}><Text style={s.link}>← 홈</Text></Pressable>
      <Text style={s.title}>설정</Text>
      <Card>
        <Text style={s.label}>매일 측정 알림 (소리)</Text>
        <Text style={s.big}>{String(hour).padStart(2, "0")}:{String(minute).padStart(2, "0")}</Text>
        <View style={[s.row, { gap: 8 }]}>
          <View style={{ flex: 1 }}><Btn title="−30분" kind="ghost" onPress={() => step(-30)} /></View>
          <View style={{ flex: 1 }}><Btn title="+30분" kind="ghost" onPress={() => step(30)} /></View>
        </View>
        <Btn title={on ? "알림 끄기" : "알림 켜기"} onPress={() => apply({ hour, minute, on: !on })} />
        <Text style={s.help}>휴대폰 안에서 예약하는 알림이라 인터넷이 없어도, 비용 없이 매일 울려요.</Text>
      </Card>
      <Card>
        <Text style={s.label}>서버</Text>
        <Text style={s.body}>{getServer()}</Text>
      </Card>
      <Btn title="로그아웃" kind="ghost" onPress={onLogout} />
    </ScrollView>
  );
}

const s = StyleSheet.create({
  page: { padding: 16, gap: 12, paddingBottom: 40 },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  logo: { fontSize: 26, fontWeight: "800", color: C.navy },
  sub: { fontSize: 15, color: C.muted, marginBottom: 6 },
  hello: { fontSize: 22, fontWeight: "800", color: C.navy, lineHeight: 30 },
  title: { fontSize: 22, fontWeight: "800", color: C.navy },
  card: { backgroundColor: C.card, borderRadius: 20, padding: 18, gap: 6 },
  label: { fontSize: 15, fontWeight: "700", color: C.muted },
  big: { fontSize: 28, fontWeight: "800", color: C.navy },
  unit: { fontSize: 15, color: C.muted, fontWeight: "600" },
  body: { fontSize: 17, color: C.ink, lineHeight: 25 },
  help: { fontSize: 14, color: C.muted, lineHeight: 20 },
  link: { fontSize: 17, color: C.blue, fontWeight: "700" },
  err: { fontSize: 15, color: C.warn, marginTop: 6 },
  banner: { borderRadius: 16, padding: 14, gap: 4 },
  bannerTitle: { fontSize: 18, fontWeight: "800", color: C.navy },
  chip: { alignSelf: "flex-start", fontSize: 14, fontWeight: "700", paddingHorizontal: 10, paddingVertical: 3, borderRadius: 999, overflow: "hidden" },
  step: { fontSize: 14, fontWeight: "700", paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, backgroundColor: C.soft, color: C.muted, overflow: "hidden" },
  tile: { flex: 1, backgroundColor: C.soft, borderRadius: 14, padding: 10, gap: 2 },
  tileName: { fontSize: 14, fontWeight: "700", color: C.navy },
  tilePlain: { fontSize: 12, color: C.muted },
  tileVal: { fontSize: 22, fontWeight: "800", color: C.navy },
  bars: { flexDirection: "row", alignItems: "flex-end", height: 130, gap: 6, marginTop: 6 },
  bar: { width: 22, borderRadius: 6 },
  barVal: { fontSize: 12, color: C.ink, marginBottom: 2 },
  barCap: { fontSize: 11, color: C.muted, marginTop: 4 },
  input: { backgroundColor: C.soft, borderRadius: 12, paddingHorizontal: 14, paddingVertical: 12, fontSize: 18, marginBottom: 8, color: C.ink },
  inputBig: { backgroundColor: C.card, borderRadius: 14, paddingHorizontal: 16, paddingVertical: 14, fontSize: 26, fontWeight: "700", color: C.navy },
  btn: { backgroundColor: C.navy, borderRadius: 999, paddingVertical: 16, alignItems: "center", marginTop: 6 },
  btnGhost: { backgroundColor: C.card, borderWidth: 1.5, borderColor: C.blueSoft },
  btnText: { color: "#fff", fontSize: 18, fontWeight: "800" },
  suggest: { fontSize: 15, color: C.blue, backgroundColor: C.blueSoft, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 999, overflow: "hidden" },
  msg: { maxWidth: "88%", padding: 12, borderRadius: 16 },
  msgUser: { alignSelf: "flex-end", backgroundColor: C.navy },
  msgBot: { alignSelf: "flex-start", backgroundColor: C.card },
  send: { backgroundColor: C.navy, borderRadius: 999, paddingHorizontal: 18, paddingVertical: 14 },
});
