const form = document.querySelector("#query-form");
const submit = document.querySelector("#submit");
const feedback = document.querySelector("#feedback");
const newSession = document.querySelector("#new-session");
let sessionId = sessionStorage.getItem("kb-query-session") || "";

function textElement(tag, text, className) {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    return node;
}

function appendTurn(question, answer) {
    const li = document.createElement("li");
    li.append(textElement("strong", question), textElement("p", answer, "history-answer"));
    document.querySelector("#history").append(li);
}

async function loadHistory() {
    if (!sessionId) return;
    try {
        const response = await fetch(`/api/queries/${encodeURIComponent(sessionId)}/history`);
        if (!response.ok) throw new Error("暂时无法读取会话历史");
        const data = await response.json();
        for (const turn of data.history) appendTurn(turn.question, turn.answer);
    } catch (error) {
        feedback.textContent = error.message;
    }
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const question = document.querySelector("#question").value.trim();
    if (!question) { feedback.textContent = "请输入问题"; return; }
    submit.disabled = true;
    newSession.disabled = true;
    document.querySelector("#result").hidden = true;
    feedback.textContent = "正在检索并生成回答，首次查询需要加载本地模型…";
    try {
        const response = await fetch("/api/queries", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({question, session_id: sessionId || null,
                use_hyde: document.querySelector("#hyde").checked})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "查询失败，请稍后重试");
        sessionId = data.session_id;
        sessionStorage.setItem("kb-query-session", sessionId);
        document.querySelector("#answer").textContent = data.answer;
        const sources = document.querySelector("#sources");
        sources.replaceChildren();
        for (const source of data.sources) {
            const li = document.createElement("li");
            const details = document.createElement("details");
            details.append(textElement("summary", `[${source.citation}] ${source.item_name || ""} · ${source.title || "正文"}`),
                textElement("p", source.content, "source-content"));
            li.append(details);
            sources.append(li);
        }
        document.querySelector("#result").hidden = false;
        appendTurn(question, data.answer);
        feedback.textContent = `完成，引用 ${data.sources.length} 个切片`;
    } catch (error) {
        feedback.textContent = error.message || "网络连接失败";
    } finally {
        submit.disabled = false;
        newSession.disabled = false;
    }
});

newSession.addEventListener("click", () => {
    sessionId = "";
    sessionStorage.removeItem("kb-query-session");
    document.querySelector("#history").replaceChildren();
    document.querySelector("#result").hidden = true;
    feedback.textContent = "已开始新会话";
});

loadHistory();
