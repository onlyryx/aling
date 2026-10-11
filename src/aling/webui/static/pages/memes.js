import { api } from "../api.js";

const make = (tag, className = "", text = "") => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text;
  return node;
};
const reasons = {
  missing_directory: "素材目录尚未创建。请由部署者创建目录并授予 bot 用户写权限。",
  unsafe_path: "素材目录或清单路径包含不安全的链接。请由部署者检查路径。",
  permission_denied: "素材目录只读。请由部署者检查写权限；Docker 默认只读，可选择启用 compose.webui.yaml。",
  invalid_catalog: "清单或上传目录无效。请先由部署者修复，面板不会覆盖损坏清单。",
};

export function createMemes(container) {
  const notice = make("p", "notice");
  notice.setAttribute("role", "status");
  const status = make("p", "help", "正在读取素材目录…");
  const uploads = make("section", "card section-card");
  uploads.append(make("h2", "", "上传一张图片"), make("p", "help", "PNG、JPEG、静态 WebP 或 GIF；最大 5 MB、1200 万像素。上传后请填写说明与标签，未整理素材不会用于检索。"), status);
  const uploadForm = make("form");
  const fileLabel = make("label", "", "选择图片");
  const file = make("input");
  file.type = "file";
  file.accept = "image/png,image/jpeg,image/webp,image/gif";
  fileLabel.append(file);
  const uploadButton = make("button", "primary", "上传图片");
  uploadButton.type = "submit";
  uploadButton.disabled = true;
  uploadForm.append(fileLabel, uploadButton);
  uploads.append(uploadForm);
  const editor = make("section", "card section-card");
  editor.hidden = true;
  const listing = make("section", "card section-card");
  listing.append(make("h2", "", "本地素材"));
  const filter = make("form", "actions");
  const searchLabel = make("label", "meme-search", "搜索说明或标签");
  const search = make("input");
  search.type = "search";
  search.maxLength = 100;
  searchLabel.append(search);
  const searchButton = make("button", "secondary", "搜索");
  searchButton.type = "submit";
  filter.append(searchLabel, searchButton);
  const count = make("p", "help");
  const grid = make("div", "meme-grid");
  const navigation = make("div", "pagination");
  const previous = make("button", "secondary", "上一页");
  const next = make("button", "secondary", "下一页");
  navigation.append(previous, next);
  listing.append(filter, count, grid, navigation);
  const dialog = make("dialog", "memory-dialog");
  dialog.append(make("h2", "", "移除素材索引？"), make("p", "", "只移除清单条目，图片文件仍保留。共享图片不会删除，上传容量也不会释放。"));
  const cancel = make("button", "secondary", "取消");
  const confirm = make("button", "primary", "确认移除索引");
  const dialogActions = make("div", "actions");
  dialogActions.append(cancel, confirm);
  dialog.append(dialogActions);
  container.append(notice, uploads, editor, listing, dialog);
  notice.hidden = true;
  let offset = 0;
  let query = "";
  let writeStatus = null;
  let selected = null;
  let removing = null;
  let uploading = false;
  let refreshVersion = 0;
  const writableControls = new Set();
  function message(text, error = false) {
    notice.textContent = text;
    notice.classList.toggle("error", error);
    notice.hidden = false;
  }
  function updatePermissions() {
    uploadButton.disabled = uploading || !writeStatus?.upload_available;
    file.disabled = uploading || !writeStatus?.upload_available;
    for (const control of writableControls) control.disabled = !writeStatus?.writable;
  }
  function edit(item) {
    selected = item.id;
    writableControls.clear();
    editor.replaceChildren(make("h2", "", item.description.trim() ? "编辑素材说明" : "未整理素材"));
    const preview = make("img", "meme-preview");
    preview.src = `/api/admin/memes/${encodeURIComponent(item.id)}/image`;
    preview.alt = item.description || "待整理素材预览";
    editor.append(preview);
    const form = make("form", "meme-editor");
    const fields = {};
    for (const [key, title, maximum, value] of [
      ["description", "说明（最多 200 字）", 200, item.description],
      ["tags", "标签（每行一个，最多 32 个，每个 80 字）", 2591, item.tags.join("\n")],
      ["source", "来源（最多 500 字）", 500, item.source],
      ["license", "许可（最多 500 字）", 500, item.license],
    ]) {
      const label = make("label", "", title);
      const input = make("textarea");
      input.maxLength = maximum;
      input.rows = key === "tags" ? 3 : 2;
      input.value = value;
      label.append(input);
      form.append(label);
      fields[key] = input;
      writableControls.add(input);
    }
    const save = make("button", "primary", "保存说明");
    save.type = "submit";
    writableControls.add(save);
    form.append(save);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const tags = fields.tags.value.split("\n").map((tag) => tag.trim()).filter(Boolean);
      if (tags.length > 32 || tags.some((tag) => tag.length > 80)) { message("标签最多 32 个，每个最多 80 字。", true); return; }
      save.disabled = true;
      try {
        await api(`/memes/${encodeURIComponent(item.id)}`, { method: "PATCH", body: { description: fields.description.value, tags, source: fields.source.value, license: fields.license.value } });
        message("素材说明已保存。留空说明的素材仍属于未整理素材。");
        await refresh();
      } catch (error) { message(error.message, true); }
      finally { if (form.isConnected) save.disabled = !writeStatus?.writable; }
    });
    editor.append(form, make("p", "help", "请仅使用自己制作或明确允许使用的素材，保留来源与许可信息。"));
    editor.hidden = false;
    updatePermissions();
    fields.description.focus();
  }
  async function refresh() {
    const version = ++refreshVersion;
    try {
      const result = await api(`/memes?offset=${offset}&limit=24&query=${encodeURIComponent(query)}`);
      if (version !== refreshVersion) return;
      writeStatus = result.write_status;
      status.textContent = writeStatus.writable ? `可写 · 已上传 ${(writeStatus.managed_bytes / 1_000_000).toFixed(2)} / 200 MB（含已移除条目保留的文件）${writeStatus.upload_available ? "" : " · 容量已满，暂停新增"}` : `只读 · ${reasons[writeStatus.reason] ?? "素材目录当前不可写。"}`;
      updatePermissions();
      count.textContent = result.total ? `共 ${result.total} 项，本页 ${result.items.length} 项` : "尚无匹配素材。";
      previous.disabled = offset === 0;
      next.disabled = offset + 24 >= result.total;
      grid.replaceChildren();
      for (const item of result.items) {
        const card = make("article", "meme-card");
        const thumbnail = make("img", "meme-preview");
        thumbnail.src = `/api/admin/memes/${encodeURIComponent(item.id)}/image`;
        thumbnail.alt = item.description || "未整理素材";
        thumbnail.loading = "lazy";
        const title = make("h3", "", item.description || "未整理素材");
        const tags = make("p", "help", item.tags.join(" · ") || "尚无标签");
        const editButton = make("button", "secondary", "编辑说明");
        editButton.addEventListener("click", () => edit(item));
        const removeButton = make("button", "secondary", "移除索引");
        removeButton.disabled = !writeStatus.writable;
        removeButton.addEventListener("click", () => { removing = item.id; dialog.showModal(); cancel.focus(); });
        const actions = make("div", "actions");
        actions.append(editButton, removeButton);
        card.append(thumbnail, title, tags, actions);
        grid.append(card);
      }
    } catch (error) {
      if (version !== refreshVersion) return;
      writeStatus = null;
      updatePermissions();
      status.textContent = "目录状态无法读取，暂停写入。";
      message(error.message, true);
    }
  }
  filter.addEventListener("submit", (event) => { event.preventDefault(); query = search.value; offset = 0; refresh(); });
  previous.addEventListener("click", () => { offset = Math.max(0, offset - 24); refresh(); });
  next.addEventListener("click", () => { offset += 24; refresh(); });
  uploadForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const image = file.files[0];
    if (!image) { message("请先选择一张图片。", true); return; }
    if (image.size > 5_000_000) { message("图片最大 5 MB。", true); return; }
    uploading = true;
    updatePermissions();
    try {
      const result = await api("/memes/upload", { method: "POST", raw: true, body: image });
      const uploaded = await api(`/memes?query=${encodeURIComponent(result.id)}`);
      const item = uploaded.items.find((entry) => entry.id === result.id);
      if (item) edit(item);
      file.value = "";
      query = "";
      search.value = "";
      offset = 0;
      message("图片已上传，请在编辑区填写说明与标签。");
      await refresh();
    } catch (error) { message(error.message, true); }
    finally { uploading = false; updatePermissions(); }
  });
  cancel.addEventListener("click", () => { dialog.close(); removing = null; });
  confirm.addEventListener("click", async () => {
    const id = removing;
    if (!id) return;
    confirm.disabled = true;
    try {
      const result = await api(`/memes/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (selected === id) { editor.hidden = true; selected = null; writableControls.clear(); }
      dialog.close();
      removing = null;
      message(result.removed ? "索引已移除，原图片仍保留。" : "该索引已不存在。原图片仍保留。");
      await refresh();
    } catch (error) { dialog.close(); message(error.message, true); }
    finally { confirm.disabled = false; }
  });
  document.addEventListener("panel-session-expired", () => { dialog.close(); removing = null; });
  return { refresh };
}
