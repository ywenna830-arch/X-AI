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

const chatForm = document.querySelector("[data-chat-form]");

if (chatForm) {
    const chatThread = document.querySelector("[data-chat-thread]");
    const chatInput = document.querySelector("[data-chat-input]");
    const submitButton = document.querySelector("[data-chat-submit]");
    const noticeDate = document.querySelector("[data-notice-date]");
    const sourceType = document.querySelector("[data-source-type]");
    const sourceFilename = document.querySelector("[data-source-filename]");
    const sourcePages = document.querySelector("[data-source-pages]");
    let isSending = false;

    const appendMessage = (text, kind) => {
        const message = document.createElement("div");
        message.className = `chat-message ${kind}-message`;

        const content = document.createElement("div");
        content.className = "message-content";

        const paragraph = document.createElement("p");
        paragraph.textContent = text;
        content.appendChild(paragraph);
        message.appendChild(content);
        chatThread.appendChild(message);
        scrollChatToBottom();
        return message;
    };

    const appendUserMessage = (text) => appendMessage(text, "user");
    const appendAssistantMessage = (text) => appendMessage(text, "assistant");
    const appendErrorMessage = (text) => appendMessage(text, "error");

    const appendLoadingMessage = () => {
        const message = appendMessage("正在回复...", "assistant");
        message.dataset.loadingMessage = "true";
        return message;
    };

    const removeLoadingMessage = () => {
        const loading = chatThread.querySelector("[data-loading-message]");
        if (loading) {
            loading.remove();
        }
    };

    const appendTaskPreview = (task, confirmUrl) => {
        const card = document.createElement("div");
        card.className = "task-preview-card";

        const course = document.createElement("span");
        course.className = "task-preview-course";
        course.textContent = task.course_name || "课程待确认";

        const title = document.createElement("strong");
        title.textContent = task.title || "任务名称待确认";

        const deadline = document.createElement("p");
        deadline.textContent = `截止：${task.deadline || "待确认"}`;

        const confidence = document.createElement("p");
        confidence.textContent = `状态：${task.confidence || "待确认"}`;

        const link = document.createElement("a");
        link.className = "button small-button";
        link.href = confirmUrl || "/tasks/confirm";
        link.textContent = "查看并确认";

        card.appendChild(course);
        card.appendChild(title);
        card.appendChild(deadline);
        card.appendChild(confidence);
        card.appendChild(link);
        chatThread.appendChild(card);
        scrollChatToBottom();
    };

    const setSending = (nextSending) => {
        isSending = nextSending;
        submitButton.disabled = nextSending;
        submitButton.classList.toggle("is-disabled", nextSending);
        submitButton.textContent = nextSending ? "整理中..." : "发送";
    };

    const scrollChatToBottom = () => {
        chatThread.scrollTop = chatThread.scrollHeight;
    };

    const sendChatMessage = async () => {
        if (isSending) {
            return;
        }

        const message = chatInput.value.trim();
        if (!message) {
            return;
        }

        appendUserMessage(message);
        setSending(true);
        appendLoadingMessage();

        try {
            const response = await fetch("/api/chat", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({
                    message,
                    notice_date: noticeDate ? noticeDate.value : "",
                    source_type: sourceType ? sourceType.value : "",
                    source_filename: sourceFilename ? sourceFilename.value : "",
                    source_pages: sourcePages ? sourcePages.value : "",
                }),
            });
            const data = await response.json();
            removeLoadingMessage();

            if (!response.ok || !data.ok) {
                appendErrorMessage(data.error || "请求失败，请稍后重试。");
                return;
            }

            appendAssistantMessage(data.reply || "我已处理。");
            if (data.type === "task") {
                appendTaskPreview(data.task_preview || {}, data.confirm_url);
            }
            chatInput.value = "";
        } catch (error) {
            removeLoadingMessage();
            appendErrorMessage("网络请求失败，请稍后重试。");
        } finally {
            setSending(false);
        }
    };

    chatForm.addEventListener("submit", (event) => {
        event.preventDefault();
        sendChatMessage();
    });

    chatInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            sendChatMessage();
        }
    });
}
