const form = document.querySelector("#import-form");
const files = document.querySelector("#files");
const feedback = document.querySelector("#feedback");
const tasks = document.querySelector("#tasks");
const submit = form.querySelector("button");
const uploadProgress = document.querySelector("#upload-progress");
const dropZone = document.querySelector("#drop-zone");
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

function upload(body) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", "/api/imports");
        xhr.responseType = "json";
        xhr.timeout = 120000;
        xhr.upload.addEventListener("progress", (event) => {
            if (event.lengthComputable) {
                uploadProgress.value = Math.round(event.loaded / event.total * 100);
                feedback.textContent = `正在上传 ${uploadProgress.value}%`;
            }
        });
        xhr.addEventListener("load", () => {
            if (xhr.status >= 200 && xhr.status < 300 && xhr.response) resolve(xhr.response);
            else reject(new Error(typeof xhr.response?.detail === "string" ? xhr.response.detail : "上传请求失败"));
        });
        xhr.addEventListener("error", () => reject(new Error("上传连接失败")));
        xhr.addEventListener("timeout", () => reject(new Error("上传超时，请检查网络后重试")));
        xhr.send(body);
    });
}

for (const eventName of ["dragenter", "dragover"]) {
    dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        if (!submit.disabled) dropZone.setAttribute("data-dragging", "");
    });
}
for (const eventName of ["dragleave", "drop"]) {
    dropZone.addEventListener(eventName, (event) => {
        event.preventDefault();
        dropZone.removeAttribute("data-dragging");
    });
}
dropZone.addEventListener("drop", (event) => {
    if (submit.disabled) return;
    const selected = Array.from(event.dataTransfer.files);
    if (!selected.length || selected.some((file) => !/\.(pdf|md|markdown)$/i.test(file.name))) {
        feedback.textContent = "请选择 PDF 或 Markdown 文件";
        return;
    }
    files.files = event.dataTransfer.files;
    feedback.textContent = `已选择 ${selected.length} 个文件`;
});

async function poll(taskId, badge, statusLine) {
    try {
        const task = await request(`/api/imports/${taskId}`);
        badge.textContent = labels[task.status] || task.status;
        badge.dataset.status = task.status;
        const details = [];
        if (task.running_list.length) details.push(`当前：${task.running_list.join("、")}`);
        if (task.done_list.length) details.push(`已完成：${task.done_list.join("、")}`);
        if (task.imported_count !== null) details.push(`入库切片：${task.imported_count}`);
        if (task.error) details.push(task.error);
        statusLine.textContent = details.join(" · ");
        if (task.status === "pending" || task.status === "processing") {
            setTimeout(() => poll(taskId, badge, statusLine), 1000);
        }
    } catch (error) {
        statusLine.textContent = `无法获取进度：${error.message}`;
    }
}

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const names = Array.from(files.files, (file) => file.name);
    submit.disabled = true;
    files.disabled = true;
    uploadProgress.hidden = false;
    uploadProgress.value = 0;
    feedback.textContent = "正在上传…";
    try {
        const body = new FormData();
        for (const file of files.files) body.append("files", file);
        const result = await upload(body);
        result.task_ids.forEach((taskId, index) => {
            const item = document.createElement("li");
            const heading = document.createElement("div");
            const title = document.createElement("strong");
            const badge = document.createElement("span");
            const identifier = document.createElement("small");
            const statusLine = document.createElement("p");
            heading.className = "task-heading";
            badge.className = "task-status";
            badge.dataset.status = "pending";
            title.textContent = names[index];
            badge.textContent = labels.pending;
            identifier.textContent = `任务编号 ${taskId}`;
            heading.append(title, badge);
            item.append(heading, identifier, statusLine);
            tasks.prepend(item);
            poll(taskId, badge, statusLine);
        });
        form.reset();
        feedback.textContent = result.message;
    } catch (error) {
        feedback.textContent = `上传失败：${error.message}`;
    } finally {
        submit.disabled = false;
        files.disabled = false;
        uploadProgress.hidden = true;
    }
});
