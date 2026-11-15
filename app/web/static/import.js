const form = document.querySelector("#import-form");
const files = document.querySelector("#files");
const feedback = document.querySelector("#feedback");
const tasks = document.querySelector("#tasks");
const submit = form.querySelector("button");
const labels = {
    pending: "等待处理",
    processing: "处理中",
    completed: "已完成",
    failed: "失败",
};

async function request(url, options) {
    const response = await fetch(url, options);
    const result = await response.json();
    if (!response.ok) {
        throw new Error(typeof result.detail === "string" ? result.detail : "请求失败");
    }
    return result;
}

async function poll(taskId, statusLine) {
    try {
        const task = await request(`/api/imports/${taskId}`);
        const details = [labels[task.status] || task.status];
        if (task.running_list.length) details.push(`当前：${task.running_list.join("、")}`);
        if (task.done_list.length) details.push(`已完成：${task.done_list.join("、")}`);
        if (task.imported_count !== null) details.push(`入库切片：${task.imported_count}`);
        if (task.error) details.push(task.error);
        statusLine.textContent = details.join(" · ");
        if (task.status === "pending" || task.status === "processing") {
            setTimeout(() => poll(taskId, statusLine), 1000);
        }
    } catch (error) {
        statusLine.textContent = `无法获取进度：${error.message}`;
    }
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const names = Array.from(files.files, (file) => file.name);
    submit.disabled = true;
    feedback.textContent = "正在上传…";
    try {
        const result = await request("/api/imports", {method: "POST", body: new FormData(form)});
        result.task_ids.forEach((taskId, index) => {
            const item = document.createElement("li");
            const title = document.createElement("strong");
            const identifier = document.createElement("small");
            const statusLine = document.createElement("span");
            title.textContent = names[index];
            identifier.textContent = `任务编号 ${taskId}`;
            statusLine.textContent = labels.pending;
            item.append(title, identifier, statusLine);
            tasks.prepend(item);
            poll(taskId, statusLine);
        });
        form.reset();
        feedback.textContent = result.message;
    } catch (error) {
        feedback.textContent = `上传失败：${error.message}`;
    } finally {
        submit.disabled = false;
    }
});
