document.documentElement.dataset.appReady = "true";

const notificationButton = document.querySelector("[data-notification-button]");

if (notificationButton) {
    const setNotificationText = (text) => {
        notificationButton.textContent = text;
    };

    if (!("Notification" in window)) {
        setNotificationText("浏览器不支持通知");
        notificationButton.disabled = true;
        notificationButton.classList.add("is-disabled");
    } else if (Notification.permission === "granted") {
        setNotificationText("浏览器通知已启用");
    } else if (Notification.permission === "denied") {
        setNotificationText("通知权限已拒绝");
        notificationButton.disabled = true;
        notificationButton.classList.add("is-disabled");
    }

    notificationButton.addEventListener("click", async () => {
        if (!("Notification" in window) || Notification.permission === "denied") {
            return;
        }
        if (Notification.permission === "granted") {
            try {
                new Notification("课迹提醒已启用", {
                    body: "网页打开时，应用内提醒会继续正常显示。",
                });
            } catch (error) {
                setNotificationText("通知发送失败");
            }
            return;
        }
        const permission = await Notification.requestPermission();
        if (permission === "granted") {
            setNotificationText("浏览器通知已启用");
        } else if (permission === "denied") {
            setNotificationText("通知权限已拒绝");
            notificationButton.disabled = true;
            notificationButton.classList.add("is-disabled");
        } else {
            setNotificationText("尚未授权通知");
        }
    });
}
