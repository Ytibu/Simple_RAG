const form = document.querySelector("#query-form");
const questionInput = document.querySelector("#question");
const conversation = document.querySelector("#history");
const chatScroll = document.querySelector("#chat-scroll");
const welcome = document.querySelector("#welcome");
const feedback = document.querySelector("#feedback");
const submit = document.querySelector("#submit");
const newSession = document.querySelector("#new-session");
const clearHistory = document.querySelector("#clear-history");
const connectionStatus = document.querySelector("#connection-status");
let sessionId = sessionStorage.getItem("kb-query-session") || "";
const nodeLabels = {
    node_item_name_confirm: "确认产品", node_search_embedding: "检索知识库",
    node_search_embedding_hyde: "增强检索", node_web_search_mcp: "联网搜索",
    node_rrf: "融合结果", node_rerank: "重排资料", node_answer_output: "生成回答"
};

function textElement(tag, text, className) {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    return node;
}

function setFeedback(message, isError = false) {
    feedback.textContent = message;
    feedback.toggleAttribute("data-error", isError);
}

function setBusy(busy) {
    submit.disabled = busy;
    newSession.disabled = busy;
    clearHistory.disabled = busy;
}

function appendTurn(question) {
    const turn = document.createElement("li");
    turn.className = "conversation-turn";
    const response = document.createElement("article");
    response.className = "turn-answer";
    turn.append(textElement("p", question, "turn-question"), response);
    conversation.append(turn);
    welcome.hidden = true;
    return response;
}

function safeUrl(value) {
    if (!value) return null;
    try {
        const url = new URL(value, location.origin);
        return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch {
        return null;
    }
}

function renderAnswer(response, answer, sources = [], state = "", images = [], candidates = []) {
    const progress = response.querySelector(".query-progress");
    response.dataset.state = state;
    response.replaceChildren(textElement("h2", "知识库回答"), textElement("p", answer, "answer-text"));
    if (progress) response.append(progress);
    if (candidates.length) {
        const options = textElement("div", "", "product-options");
        for (const name of candidates) {
            const button = textElement("button", name);
            button.type = "button";
            button.addEventListener("click", () => {
                if (submit.disabled) return;
                questionInput.value = name;
                form.requestSubmit();
            });
            options.append(button);
        }
        response.append(options);
    }
    if (images.length) {
        const gallery = textElement("div", "", "answer-images");
        for (const [index, value] of images.entries()) {
            const url = safeUrl(value);
            if (!url) continue;
            const link = document.createElement("a");
            link.href = url;
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            const image = document.createElement("img");
            image.src = url;
            image.alt = `引用资料图片 ${index + 1}`;
            image.loading = "lazy";
            link.append(image);
            gallery.append(link);
        }
        response.append(gallery);
    }
    if (sources.length) {
        const references = textElement("section", "", "turn-sources");
        const list = document.createElement("ul");
        for (const source of sources) {
            const item = document.createElement("li");
            const details = document.createElement("details");
            const title = [source.item_name, source.title].filter(Boolean).join(" · ") || "正文";
            details.append(textElement("summary", `[${source.citation}] ${title}`),
                textElement("p", source.content, "source-content"));
            const url = safeUrl(source.url);
            if (url) {
                const link = textElement("a", "打开网页来源");
                link.href = url;
                link.target = "_blank";
                link.rel = "noopener noreferrer";
                details.append(link);
            }
            item.append(details);
            list.append(item);
        }
        references.append(textElement("h3", `引用原文（${sources.length}）`), list);
        response.append(references);
    }
}

function renderProgress(response, data) {
    let details = response.querySelector(".query-progress");
    if (!details) {
        details = textElement("details", "", "query-progress");
        details.append(textElement("summary", "处理进度"), document.createElement("ul"));
        response.append(details);
    }
    const items = data.done_list.map((node) => textElement("li", `${nodeLabels[node] || node} · 已完成`));
    items.push(...data.running_list.map((node) => textElement("li", `${nodeLabels[node] || node} · 处理中`)));
    details.querySelector("ul").replaceChildren(...items);
    details.querySelector("summary").textContent = data.running_list.length
        ? data.running_list.map((node) => nodeLabels[node] || node).join("、") + "…" : "处理进度";
}

async function request(url, options) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "请求失败，请稍后重试");
    return data;
}

function receiveStream(task, reply) {
    return new Promise((resolve, reject) => {
        const stream = new EventSource(task.stream_url);
        let text = "";
        const timeout = setTimeout(() => finish(new Error("查询等待超时，可刷新页面读取历史结果")), 15 * 60 * 1000);
        function finish(error, result) {
            clearTimeout(timeout);
            stream.close();
            connectionStatus.textContent = "服务已连接";
            if (error) reject(error);
            else resolve(result);
        }
        stream.addEventListener("ready", () => { connectionStatus.textContent = "实时连接已建立"; });
        stream.addEventListener("progress", (event) => renderProgress(reply, JSON.parse(event.data)));
        stream.addEventListener("delta", (event) => {
            text += JSON.parse(event.data).text;
            reply.querySelector(".answer-text").textContent = text;
            chatScroll.scrollTop = chatScroll.scrollHeight;
        });
        stream.addEventListener("final", (event) => finish(null, JSON.parse(event.data)));
        stream.addEventListener("error", (event) => {
            if (event.data) finish(new Error(JSON.parse(event.data).error));
            else if (stream.readyState === EventSource.CLOSED) finish(new Error("实时连接已关闭"));
            else connectionStatus.textContent = "连接中断，正在重连…";
        });
    });
}

function showResult(reply, data) {
    renderAnswer(reply, data.answer, data.sources || [], "", data.image_urls || [],
        data.item_names?.length ? [] : data.item_candidates || []);
    chatScroll.scrollTop = chatScroll.scrollHeight;
}

async function loadHistory() {
    if (!sessionId) return;
    setBusy(true);
    try {
        const data = await request(`/api/queries/${encodeURIComponent(sessionId)}/history`);
        let pendingReply;
        const pending = JSON.parse(sessionStorage.getItem("kb-query-pending") || "null");
        let completed = false;
        for (const turn of data.history) {
            const reply = appendTurn(turn.question);
            showResult(reply, turn);
            if (pending && turn.task_id === pending.task_id) {
                pendingReply = reply;
                completed = turn.status !== "processing";
            }
        }
        if (pending && !completed) {
            const reply = pendingReply || appendTurn(pending.question);
            renderAnswer(reply, "正在继续接收回答…", [], "pending");
            showResult(reply, await receiveStream(pending, reply));
        }
        sessionStorage.removeItem("kb-query-pending");
    } catch (error) {
        setFeedback(error.message, true);
    } finally {
        setBusy(false);
    }
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    const draft = questionInput.value;
    const question = draft.trim();
    if (!question) {
        setFeedback("请输入问题", true);
        questionInput.focus();
        return;
    }
    setBusy(true);
    setFeedback("");
    const reply = appendTurn(question);
    renderAnswer(reply, "正在检索资料并生成回答…", [], "pending");
    chatScroll.scrollTop = chatScroll.scrollHeight;
    questionInput.value = "";
    questionInput.focus();
    try {
        const streaming = document.querySelector("#stream-answer").checked;
        let data = await request(streaming ? "/api/queries/submit" : "/api/queries", {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({question, session_id: sessionId || null,
                use_hyde: document.querySelector("#hyde").checked,
                use_web: document.querySelector("#web-search").checked})
        });
        sessionId = data.session_id;
        sessionStorage.setItem("kb-query-session", sessionId);
        if (streaming) {
            sessionStorage.setItem("kb-query-pending", JSON.stringify({...data, question}));
            data = await receiveStream(data, reply);
        }
        showResult(reply, data);
        sessionStorage.removeItem("kb-query-pending");
    } catch (error) {
        renderAnswer(reply, `发送失败：${error.message || "网络连接失败"}`, [], "error");
        sessionStorage.removeItem("kb-query-pending");
        if (!questionInput.value) questionInput.value = draft;
    } finally {
        setBusy(false);
    }
});

questionInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.ctrlKey || event.altKey ||
        event.metaKey || event.isComposing || event.keyCode === 229) return;
    event.preventDefault();
    if (!submit.disabled) form.requestSubmit();
});

document.querySelectorAll("[data-question]").forEach((button) => {
    button.addEventListener("click", () => {
        questionInput.value = button.dataset.question;
        questionInput.focus();
    });
});

function resetConversation() {
    sessionId = "";
    sessionStorage.removeItem("kb-query-session");
    sessionStorage.removeItem("kb-query-pending");
    conversation.replaceChildren();
    welcome.hidden = false;
    questionInput.value = "";
    questionInput.focus();
}

newSession.addEventListener("click", () => {
    resetConversation();
    setFeedback("已开始新会话");
});

clearHistory.addEventListener("click", async () => {
    if (!sessionId) return;
    setBusy(true);
    try {
        await request(`/api/queries/${encodeURIComponent(sessionId)}/history`, {method: "DELETE"});
        resetConversation();
        setFeedback("会话历史已清空");
    } catch (error) {
        setFeedback(error.message, true);
    } finally {
        setBusy(false);
    }
});

request("/health").then(() => { connectionStatus.textContent = "服务已连接"; })
    .catch(() => { connectionStatus.textContent = "服务连接失败"; });
loadHistory();
