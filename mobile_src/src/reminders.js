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
  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("daily", {
      name: "매일 측정 알림",
      importance: Notifications.AndroidImportance.HIGH,   // 소리 + 화면 위 알림
      sound: "default",
      vibrationPattern: [0, 400, 200, 400],
    });
  }
  await Notifications.cancelAllScheduledNotificationsAsync();
  await Notifications.scheduleNotificationAsync({
    content: {
      title: "이음 · 혈압과 체중을 잴 시간이에요",
      body: "소변을 본 뒤, 약 먹기 전에 5분 앉아 쉬고 재 주세요.",
      sound: "default",
    },
    trigger: { type: Notifications.SchedulableTriggerInputTypes.DAILY, hour, minute, channelId: "daily" },
  });
  return true;
}

export async function cancelDaily() {
  await Notifications.cancelAllScheduledNotificationsAsync();
}
