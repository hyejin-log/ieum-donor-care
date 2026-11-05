// 매일 같은 시각에 소리 나는 알림 (휴대폰 안에서 예약하므로 인터넷·비용 없이 동작)
import { Platform } from "react-native";
import * as Notifications from "expo-notifications";

Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldPlaySound: true, shouldSetBadge: false, shouldShowBanner: true, shouldShowList: true,
  }),
});

export async function scheduleDaily(hour, minute) {
  const { status } = await Notifications.requestPermissionsAsync();
  if (status !== "granted") return false;
  let channelId;
  if (Platform.OS === "android") {
    // 채널의 중요도 HIGH = 소리 + 화면 위 알림. 소리는 휴대폰 기본 알림음을 쓴다.
    // (sound: "default" 를 넣으면 일부 기기 · Expo Go 에서 NullPointerException 이 나서 넣지 않는다)
    try {
      await Notifications.setNotificationChannelAsync("daily-measure", {
        name: "매일 측정 알림",
        importance: Notifications.AndroidImportance.HIGH,
        enableVibrate: true,
      });
      channelId = "daily-measure";
    } catch (e) {
      console.warn("알림 채널을 만들지 못해 기본 채널로 보냅니다", e);
    }
  }
  await Notifications.cancelAllScheduledNotificationsAsync();
  await Notifications.scheduleNotificationAsync({
    content: {
      title: "이음 · 혈압과 체중을 잴 시간이에요",
      body: "소변을 본 뒤, 약 먹기 전에 5분 앉아 쉬고 재 주세요.",
      sound: "default",
    },
    trigger: { type: Notifications.SchedulableTriggerInputTypes.DAILY, hour, minute, ...(channelId ? { channelId } : {}) },
  });
  return true;
}

export async function cancelDaily() {
  await Notifications.cancelAllScheduledNotificationsAsync();
}
