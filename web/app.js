/* Image to PDF Converter — client side.
   Page order lives in `state.items`; every view is re-rendered from it. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);

  // ?demo=1 hides the personal quick-access folder shortcuts, so screenshots
  // and recordings do not show anyone's home directory. See README.
  const params = new URLSearchParams(location.search);
  const demoMode = params.get("demo") === "1";

  const state = {
    root: null,
    items: [],
    revision: 0,
    dims: Object.create(null),
    draggingId: null,
    browserDir: null,
    browserImages: [],
    browserFolders: [],
    exportCache: null,
    busy: false,
  };

  /* ------------------------------------------------------------ utilities */

  let seq = 0;
  const nextId = () => `item-${++seq}`;

  const naturalKey = (text) =>
    String(text)
      .toLowerCase()
      .split(/(\d+)/)
      .map((part) => (/^\d+$/.test(part) ? [1, parseInt(part, 10)] : [0, part]))
      .map((entry) => entry[1]);

  const compareNatural = (a, b) => {
    const left = naturalKey(a);
    const right = naturalKey(b);
    for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
      const x = left[i];
      const y = right[i];
      if (x === undefined) return -1;
      if (y === undefined) return 1;
      if (typeof x === "number" && typeof y === "number") {
        if (x !== y) return x - y;
        continue;
      }
      const sx = String(x);
      const sy = String(y);
      if (sx !== sy) return sx < sy ? -1 : 1;
    }
    return 0;
  };

  const fmtBytes = (bytes) => {
    if (!bytes && bytes !== 0) return "";
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${value < 10 && unit > 0 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
  };

  const thumbUrl = (item, size) => {
    if (item.kind === "file") {
      return item.objectUrl;
    }
    const rotation = item.rotation || 0;
    return `/api/thumb?path=${encodeURIComponent(item.path)}&size=${size}&rotation=${rotation}`;
  };

  const setStatus = (text) => {
    $("status-text").textContent = text;
  };

  function toast(message, tone = "") {
    const node = document.createElement("div");
    node.className = `toast ${tone}`;
    node.textContent = message;
    $("toasts").appendChild(node);
    setTimeout(() => {
      node.style.transition = "opacity .3s ease";
      node.style.opacity = "0";
      setTimeout(() => node.remove(), 320);
    }, tone === "error" ? 6000 : 3200);
  }

  async function apiJson(path, options = {}) {
    const response = await fetch(path, options);
    const text = await response.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = null;
    }
    if (!response.ok) {
      throw new Error((data && data.error) || `Request failed (${response.status})`);
    }
    return data;
  }

  /* ------------------------------------------------------- state changes */

  function addPaths(paths, options = {}) {
    const known = new Set(state.items.filter((i) => i.kind === "path").map((i) => i.path.toLowerCase()));
    const added = [];
    for (const entry of paths) {
      const path = typeof entry === "string" ? entry : entry.path;
      const key = String(path).toLowerCase();
      if (options.dedupe !== false && known.has(key)) continue;
      known.add(key);
      added.push({
        id: nextId(),
        kind: "path",
        path,
        name: (entry && entry.name) || path.replace(/\\/g, "/").split("/").pop(),
        relPath: (entry && entry.relPath) || (entry && entry.name) || path,
        size: entry && entry.size,
        mtime: entry && entry.mtime,
        rotation: 0,
        failed: false,
      });
    }
    state.items.push(...added);
    return added.length;
  }

  function addFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return 0;
    for (const file of files) {
      state.items.push({
        id: nextId(),
        kind: "file",
        file,
        name: file.webkitRelativePath || file.name,
        relPath: file.webkitRelativePath || file.name,
        size: file.size,
        mtime: file.lastModified / 1000,
        rotation: 0,
        objectUrl: URL.createObjectURL(file),
        failed: false,
      });
    }
    return files.length;
  }

  function clearAll() {
    for (const item of state.items) {
      if (item.objectUrl) URL.revokeObjectURL(item.objectUrl);
    }
    state.items = [];
    state.exportCache = null;
    render();
  }

  function removeItem(id) {
    const index = state.items.findIndex((i) => i.id === id);
    if (index === -1) return;
    const [item] = state.items.splice(index, 1);
    if (item.objectUrl) URL.revokeObjectURL(item.objectUrl);
    state.exportCache = null;
    render();
  }

  function moveItem(id, targetIndex) {
    const from = state.items.findIndex((i) => i.id === id);
    if (from === -1) return;
    let to = Math.max(0, Math.min(state.items.length - 1, targetIndex));
    if (from === to) return;
    const [item] = state.items.splice(from, 1);
    state.items.splice(to, 0, item);
    state.exportCache = null;
    render();
  }

  function sortItems(key, silent) {
    const sign = key === "name-desc" ? -1 : 1;
    if (key === "name" || key === "name-desc") {
      state.items.sort((a, b) => sign * compareNatural(a.relPath || a.name, b.relPath || b.name));
    } else if (key === "mtime") {
      state.items.sort((a, b) => (a.mtime || 0) - (b.mtime || 0));
    } else if (key === "size") {
      state.items.sort((a, b) => (a.size || 0) - (b.size || 0));
    }
    if (!silent) {
      state.exportCache = null;
      render();
      setStatus(`Sorted ${state.items.length} page(s) by ${$("sort-key").selectedOptions[0].textContent}.`);
    }
  }

  /* ------------------------------------------------------------ rendering */

  function pageLabel(index) {
    return String(index + 1);
  }

  function buildCard(item, index) {
    const card = document.createElement("article");
    card.className = "card";
    card.dataset.id = item.id;
    card.draggable = true;

    const thumb = document.createElement("div");
    thumb.className = "thumb";

    const badge = document.createElement("span");
    badge.className = "badge";
    badge.textContent = pageLabel(index);
    thumb.appendChild(badge);

    if (item.rotation) {
      const rot = document.createElement("span");
      rot.className = "rot";
      rot.textContent = `${item.rotation}°`;
      thumb.appendChild(rot);
    }

    if (item.failed) {
      const broken = document.createElement("div");
      broken.style.cssText = "font-size:12px;color:#b23;padding:10px;text-align:center";
      broken.textContent = "Preview unavailable";
      thumb.appendChild(broken);
    } else {
      const img = document.createElement("img");
      img.loading = "lazy";
      img.decoding = "async";
      img.alt = item.name;
      img.src = `${thumbUrl(item, 360)}${item.kind === "path" ? `&v=${state.revision}-${item.rotation || 0}` : ""}`;
      img.addEventListener("load", () => {
        // Remember real pixel dimensions so "Auto-rotate" works even after a re-render.
        state.dims[item.id] = { w: img.naturalWidth, h: img.naturalHeight };
      });
      img.addEventListener("error", () => {
        item.failed = true;
        img.remove();
        const broken = document.createElement("div");
        broken.style.cssText = "font-size:12px;color:#b23;padding:10px;text-align:center";
        broken.textContent = "Cannot read image";
        thumb.appendChild(broken);
      });
      img.addEventListener("click", () => openLightbox(item));
      thumb.appendChild(img);
    }

    const info = document.createElement("div");
    info.className = "info";
    const fname = document.createElement("div");
    fname.className = "fname";
    fname.textContent = item.relPath || item.name;
    fname.title = item.kind === "file" ? `Uploaded: ${item.name}` : item.path;
    const fmeta = document.createElement("div");
    fmeta.className = "fmeta";
    fmeta.textContent = [item.size ? fmtBytes(item.size) : null, item.kind === "file" ? "added file" : "on disk"]
      .filter(Boolean)
      .join(" · ");
    info.append(fname, fmeta);

    const actions = document.createElement("div");
    actions.className = "actions";
    const make = (label, title, handler, danger) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.title = title;
      if (danger) button.className = "danger";
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        handler();
      });
      return button;
    };
    actions.append(
      make("⟲", "Rotate left 90°", () => rotateItem(item.id, -90)),
      make("⟳", "Rotate right 90°", () => rotateItem(item.id, 90)),
      make("◀", "Move one page earlier", () => moveItem(item.id, index - 1)),
      make("▶", "Move one page later", () => moveItem(item.id, index + 1)),
      make("✕", "Remove", () => removeItem(item.id), true)
    );

    const grip = document.createElement("div");
    grip.className = "grip";

    card.append(thumb, info, actions, grip);
    attachDrag(card, item);
    return card;
  }

  function render() {
    const grid = $("grid");
    grid.replaceChildren();
    state.items.forEach((item, index) => grid.appendChild(buildCard(item, index)));

    const total = state.items.length;
    $("count-label").textContent = total === 1 ? "1 page" : `${total} pages`;
    $("btn-export").disabled = total === 0 || state.busy;
    $("hint").hidden = total > 0;
    $("sort-key").disabled = total === 0;

    const bytes = state.items.reduce((sum, item) => sum + (item.size || 0), 0);
    if (total > 0) {
      setStatus(`${total} page(s) queued${bytes ? ` · ${fmtBytes(bytes)} of image data` : ""}.`);
    } else {
      setStatus("Ready. Open a folder or drop images to begin.");
    }
  }

  function rotateItem(id, delta) {
    const item = state.items.find((i) => i.id === id);
    if (!item) return;
    item.rotation = (((item.rotation || 0) + delta) % 360 + 360) % 360;
    item.failed = false;
    state.exportCache = null;
    state.revision += 1;
    render();
  }

  /* -------------------------------------------------------- drag and drop */

  let dropTarget = null;
  let dropAfter = false;

  function clearDropMarks() {
    for (const node of document.querySelectorAll(".card.drop-before, .card.drop-after")) {
      node.classList.remove("drop-before", "drop-after");
    }
    dropTarget = null;
    dropAfter = false;
  }

  function attachDrag(card, item) {
    card.addEventListener("dragstart", (event) => {
      state.draggingId = item.id;
      card.classList.add("dragging");
      event.dataTransfer.effectAllowed = "move";
      try {
        event.dataTransfer.setData("text/plain", item.id);
      } catch {
        /* ignore */
      }
    });

    card.addEventListener("dragend", () => {
      card.classList.remove("dragging");
      clearDropMarks();
      state.draggingId = null;
    });

    card.addEventListener("dragover", (event) => {
      if (!state.draggingId || state.draggingId === item.id) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      const box = card.getBoundingClientRect();
      const after = event.clientX - box.left > box.width / 2;
      if (dropTarget === card && dropAfter === after) return;
      clearDropMarks();
      card.classList.add(after ? "drop-after" : "drop-before");
      dropTarget = card;
      dropAfter = after;
    });

    card.addEventListener("drop", (event) => {
      if (!state.draggingId) return;
      event.preventDefault();
      event.stopPropagation();
      const targetId = item.id;
      const after = dropAfter;
      clearDropMarks();
      const from = state.items.findIndex((i) => i.id === state.draggingId);
      let to = state.items.findIndex((i) => i.id === targetId);
      if (from === -1 || to === -1) return;
      if (after) to += 1;
      if (from < to) to -= 1;
      moveItem(state.draggingId, to);
      $("sort-key").value = "manual";
    });
  }

  /* -------------------------------------------------------- export to PDF */

  function pageSettings() {
    return {
      pageSize: $("page-size").value,
      orientation: $("page-orientation").value,
      fit: $("page-fit").value,
      marginMm: Number($("page-margin").value),
      quality: Number($("page-quality").value),
    };
  }

  function pageSettingsKey() {
    return JSON.stringify(pageSettings());
  }

  function itemsKey() {
    return JSON.stringify(
      state.items.map((item) => [item.kind, item.path || item.name, item.rotation, item.size, item.mtime])
    );
  }

  function setBusy(busy, message) {
    state.busy = busy;
    $("btn-export").disabled = busy || state.items.length === 0;
    $("btn-export").textContent = busy ? "Building…" : "Export PDF";
    $("build-status").textContent = busy ? message || "Rendering pages…" : "";
  }

  function download(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);
  }

  function safeName(text) {
    const cleaned = String(text || "scanned-pages")
      .replace(/[\\/:*?"<>|\r\n\t]+/g, "_")
      .replace(/\.pdf$/i, "")
      .trim();
    return cleaned || "scanned-pages";
  }

  async function exportPdf() {
    if (!state.items.length || state.busy) return;
    const filename = safeName($("file-name").value) || "scanned-pages";
    const key = `${itemsKey()}|${pageSettingsKey()}`;
    setBusy(true, `Rendering ${state.items.length} page(s)…`);

    try {
      if (state.exportCache && state.exportCache.key === key) {
        download(state.exportCache.blob, `${filename}.pdf`);
        setBusy(false);
        $("last-result").textContent = `Re-downloaded (cached): ${filename}.pdf`;
        toast("PDF ready — re-downloaded from cache.", "ok");
        return;
      }

      if (state.items.some((item) => item.kind === "path")) {
        const payload = {
          filename,
          page: pageSettings(),
          items: state.items.map((item) => ({
            kind: item.kind,
            path: item.path || item.name,
            name: item.relPath || item.name,
            rotation: item.rotation || 0,
          })),
        };
        const response = await fetch("/api/build", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          const text = await response.text();
          let message = `Build failed (${response.status})`;
          try {
            message = JSON.parse(text).error || message;
          } catch {
            /* keep default */
          }
          throw new Error(message);
        }
        await finishExport(response, filename, key);
      } else {
        const form = new FormData();
        for (const item of state.items) {
          // The item id is the field name, so duplicate basenames cannot collide.
          form.append(item.id, item.file, item.name.split("/").pop());
        }
        form.append(
          "items",
          JSON.stringify(
            state.items.map((item) => ({
              path: item.id,
              name: item.name,
              rotation: item.rotation || 0,
            }))
          )
        );
        form.append("page", JSON.stringify(pageSettings()));
        form.append("filename", filename);
        const response = await fetch("/api/build-multipart", { method: "POST", body: form });
        if (!response.ok) {
          const text = await response.text();
          let message = `Build failed (${response.status})`;
          try {
            message = JSON.parse(text).error || message;
          } catch {
            /* keep default */
          }
          throw new Error(message);
        }
        await finishExport(response, filename, key);
      }
    } catch (error) {
      setBusy(false);
      $("build-status").textContent = "";
      $("last-result").textContent = "";
      toast(error.message || String(error), "error");
      setStatus(`Export failed: ${error.message || error}`);
    }
  }

  async function finishExport(response, filename, key) {
    const blob = await response.blob();
    const pages = response.headers.get("X-Page-Count");
    const seconds = response.headers.get("X-Build-Seconds");
    state.exportCache = { key, blob };
    download(blob, `${filename}.pdf`);
    setBusy(false);
    const size = fmtBytes(blob.size);
    $("last-result").textContent = `Saved ${filename}.pdf · ${pages || state.items.length} page(s) · ${size}${
      seconds ? ` · ${seconds}s` : ""
    }`;
    toast(`PDF ready: ${filename}.pdf (${pages || state.items.length} pages)`, "ok");
    setStatus(`Exported ${filename}.pdf — ${pages || state.items.length} page(s), ${size}.`);
  }

  /* ------------------------------------------------------------- lightbox */

  function openLightbox(item) {
    $("lightbox-img").src = `${thumbUrl(item, 1400)}${item.kind === "path" ? `&v=${state.revision}` : ""}`;
    $("lightbox-caption").textContent = `${item.relPath || item.name}${item.rotation ? ` · rotated ${item.rotation}°` : ""}`;
    $("lightbox").hidden = false;
  }

  function closeLightbox() {
    $("lightbox").hidden = true;
    $("lightbox-img").src = "";
  }

  /* ------------------------------------------------------- folder picking */

  async function openNativeFolder() {
    setStatus("Waiting for the folder dialog…");
    try {
      const result = await apiJson("/api/pick-folder", { method: "POST" });
      if (result.cancelled || !result.path) {
        setStatus("Folder selection cancelled.");
        return;
      }
      await loadFolder(result.path);
    } catch (error) {
      toast(`Native folder dialog unavailable: ${error.message}. Using the in-app browser.`, "error");
      openBrowser();
    }
  }

  async function loadFolder(dir, recursive) {
    const useRecursive = recursive === undefined ? true : recursive;
    setStatus(`Scanning ${dir}…`);
    const data = await apiJson(`/api/list?dir=${encodeURIComponent(dir)}&recursive=${useRecursive ? 1 : 0}`);
    if (!data.images.length) {
      toast(`No images found in ${dir}.`, "error");
      setStatus(`No supported images in ${dir}.`);
      return;
    }
    const added = addPaths(data.images, { dedupe: false });
    const sortKey = $("sort-key").value;
    if (sortKey !== "manual") sortItems(sortKey, true);
    state.exportCache = null;
    state.revision += 1;
    render();
    setStatus(`Added ${added} image(s) from ${dir}${data.truncated ? " (list truncated)" : ""}.`);
    toast(`Added ${added} page(s) from ${data.dir.split(/[\\/]/).pop() || data.dir}`, "ok");
  }

  async function pickFolderInBrowser() {
    // Chromium exposes a folder picker through the directory input.
    $("folder-input").click();
  }

  /* ---------------------------------------------------- in-app file browser */

  function openBrowser() {
    $("browser-panel").hidden = false;
    document.querySelector(".layout").classList.add("browser-open");
    $("browser-note").hidden = !demoMode;
    if (!state.browserDir) {
      apiJson("/api/health").then((health) => {
        state.root = health;
        const roots = demoMode ? demoRoots(health.roots || []) : health.roots || [];
        renderRoots(roots);
        const start = roots[1] || roots[0];
        if (start) browseTo(start);
      });
    } else if (demoMode) {
      $("browser-roots").querySelectorAll('button[data-kind="personal"]').forEach((b) => b.remove());
      $("browser-note").hidden = false;
    }
  }

  /** In demo mode only the workspace and drive roots are offered. */
  function demoRoots(roots) {
    return roots.filter((root) => !/Desktop|Downloads|Documents|Pictures|Users[\\/][^\\/]+$/i.test(root));
  }

  function closeBrowser() {
    $("browser-panel").hidden = true;
    document.querySelector(".layout").classList.remove("browser-open");
  }

  function renderRoots(roots) {
    const container = $("browser-roots");
    container.replaceChildren();
    roots.slice(0, 8).forEach((root) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = root.replace(/\\$/, "") || root;
      button.title = root;
      button.addEventListener("click", () => browseTo(root));
      container.appendChild(button);
    });
  }

  async function browseTo(dir) {
    $("browser-list").replaceChildren(Object.assign(document.createElement("div"), {
      className: "browser-item",
      textContent: "Loading…",
    }));
    try {
      const recursive = $("browser-recursive").checked;
      const [listing, folders] = await Promise.all([
        apiJson(`/api/list?dir=${encodeURIComponent(dir)}&recursive=${recursive ? 1 : 0}`),
        apiJson(`/api/subfolders?dir=${encodeURIComponent(dir)}`),
      ]);
      state.browserDir = listing.dir;
      state.browserImages = listing.images;
      state.browserFolders = folders.folders || [];
      renderBrowser();
    } catch (error) {
      toast(error.message, "error");
      setStatus(`Cannot open ${dir}: ${error.message}`);
    }
  }

  function renderBrowser() {
    const list = $("browser-list");
    list.replaceChildren();
    $("browser-path").textContent = state.browserDir;
    $("browser-add").disabled = state.browserImages.length === 0;
    $("browser-add").textContent = `Add all ${state.browserImages.length} shown`;

    const parent = state.browserDir.replace(/[\\/][^\\/]+[\\/]?$/, "");
    if (parent && parent !== state.browserDir) {
      const up = document.createElement("div");
      up.className = "browser-item up";
      up.innerHTML = '<span class="grow">↰ Parent folder</span>';
      up.addEventListener("click", () => browseTo(parent));
      list.appendChild(up);
    }

    state.browserFolders.forEach((folder) => {
      const row = document.createElement("div");
      row.className = "browser-item";
      const icon = document.createElement("span");
      icon.textContent = "📁";
      const name = document.createElement("span");
      name.className = "grow";
      name.textContent = folder.name;
      const count = document.createElement("span");
      count.className = "sub";
      count.textContent = folder.imageCount ? `${folder.imageCount}` : "";
      row.append(icon, name, count);
      row.addEventListener("dblclick", () => browseTo(folder.path));
      row.addEventListener("click", () => browseTo(folder.path));
      list.appendChild(row);
    });

    state.browserImages.slice(0, 400).forEach((image) => {
      const row = document.createElement("div");
      row.className = "browser-item";
      const img = document.createElement("img");
      img.loading = "lazy";
      img.src = `/api/thumb?path=${encodeURIComponent(image.path)}&size=72`;
      const name = document.createElement("span");
      name.className = "grow";
      name.textContent = image.relPath;
      name.title = image.path;
      const add = document.createElement("button");
      add.className = "icon-btn";
      add.type = "button";
      add.textContent = "＋";
      add.title = "Add this image";
      add.addEventListener("click", (event) => {
        event.stopPropagation();
        addPaths([image], { dedupe: true });
        render();
        toast(`Added ${image.name}`, "ok");
      });
      row.append(img, name, add);
      row.addEventListener("dblclick", () => {
        addPaths([image], { dedupe: true });
        render();
      });
      list.appendChild(row);
    });
  }

  /* ------------------------------------------------------------ wiring up */

  function bind() {
    $("btn-open-folder").addEventListener("click", openNativeFolder);
    $("btn-open-browser").addEventListener("click", () => {
      if ($("browser-panel").hidden) openBrowser();
      else closeBrowser();
    });
    $("browser-close").addEventListener("click", closeBrowser);
    $("browser-recursive").addEventListener("change", () => {
      if (state.browserDir) browseTo(state.browserDir);
    });
    $("browser-add").addEventListener("click", () => {
      const added = addPaths(state.browserImages, { dedupe: true });
      const sortKey = $("sort-key").value;
      if (sortKey !== "manual") sortItems(sortKey, true);
      state.revision += 1;
      render();
      toast(added ? `Added ${added} image(s)` : "Those images are already in the list.", added ? "ok" : "");
    });

    $("btn-add-files").addEventListener("click", () => $("file-input").click());
    $("btn-add-folder").addEventListener("click", () => pickFolderInBrowser());
    $("file-input").addEventListener("change", (event) => {
      const count = addFiles(event.target.files);
      event.target.value = "";
      if (count) {
        const sortKey = $("sort-key").value;
        if (sortKey !== "manual") sortItems(sortKey, true);
        render();
        toast(`Added ${count} image(s)`, "ok");
      }
    });

    $("folder-input").addEventListener("change", (event) => {
      const count = addFiles(event.target.files);
      event.target.value = "";
      if (count) {
        const sortKey = $("sort-key").value;
        if (sortKey !== "manual") sortItems(sortKey, true);
        render();
        toast(`Added ${count} image(s) from folder`, "ok");
      }
    });

    $("btn-clear").addEventListener("click", () => {
      if (!state.items.length) return;
      if (confirm(`Remove all ${state.items.length} page(s) from the list?`)) clearAll();
    });

    $("btn-sort").addEventListener("click", () => sortItems($("sort-key").value));
    $("sort-key").addEventListener("change", () => sortItems($("sort-key").value));

    $("btn-reverse").addEventListener("click", () => {
      state.items.reverse();
      state.exportCache = null;
      $("sort-key").value = "manual";
      render();
      setStatus("Order reversed.");
    });

    $("btn-shuffle").addEventListener("click", () => {
      for (let i = state.items.length - 1; i > 0; i -= 1) {
        const j = Math.floor(Math.random() * (i + 1));
        [state.items[i], state.items[j]] = [state.items[j], state.items[i]];
      }
      state.exportCache = null;
      $("sort-key").value = "manual";
      render();
      setStatus("Shuffled.");
    });

    $("btn-auto-orient").addEventListener("click", () => {
      let changed = 0;
      let unknown = 0;
      for (const item of state.items) {
        const dims = state.dims[item.id];
        if (!dims || !dims.w) {
          unknown += 1;
          continue;
        }
        const want = dims.w > dims.h ? 90 : 0;
        if ((item.rotation || 0) !== want) {
          item.rotation = want;
          changed += 1;
        }
      }
      state.revision += 1;
      state.exportCache = null;
      render();
      if (!changed) {
        toast(
          unknown ? "Thumbnails are still loading — try again in a moment." : "Nothing needed rotating.",
          unknown ? "error" : ""
        );
        return;
      }
      toast(`Turned ${changed} landscape image(s) into portrait pages.`, "ok");
    });

    $("btn-export").addEventListener("click", exportPdf);

    $("page-margin").addEventListener("input", (event) => {
      $("margin-value").textContent = `${event.target.value} mm`;
      state.exportCache = null;
    });
    $("page-quality").addEventListener("input", (event) => {
      $("quality-value").textContent = event.target.value;
      state.exportCache = null;
    });
    for (const id of ["page-size", "page-orientation", "page-fit", "file-name"]) {
      $(id).addEventListener("change", () => {
        state.exportCache = null;
      });
    }

    $("lightbox").addEventListener("click", (event) => {
      if (event.target.id === "lightbox" || event.target.id === "lightbox-close") closeLightbox();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeLightbox();
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        exportPdf();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "o") {
        event.preventDefault();
        openNativeFolder();
      }
    });

    // Drag files in from the OS, and drop them onto the page grid.
    let dragDepth = 0;
    window.addEventListener("dragenter", (event) => {
      if (!event.dataTransfer || !Array.from(event.dataTransfer.types).includes("Files")) return;
      dragDepth += 1;
      $("drop-overlay").hidden = false;
    });
    window.addEventListener("dragover", (event) => {
      if (event.dataTransfer && Array.from(event.dataTransfer.types).includes("Files")) {
        event.preventDefault();
      }
    });
    window.addEventListener("dragleave", () => {
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) $("drop-overlay").hidden = true;
    });
    window.addEventListener("drop", (event) => {
      if (!event.dataTransfer) return;
      const files = Array.from(event.dataTransfer.files || []);
      if (!files.length) return; // internal card drag
      event.preventDefault();
      dragDepth = 0;
      $("drop-overlay").hidden = true;
      const count = addFiles(files);
      if (count) {
        const sortKey = $("sort-key").value;
        if (sortKey !== "manual") sortItems(sortKey, true);
        render();
        toast(`Added ${count} dropped image(s)`, "ok");
      }
    });

    // The grid handles drops that are not over a card (append to the end).
    const grid = $("grid");
    grid.addEventListener("dragover", (event) => {
      if (state.draggingId) event.preventDefault();
    });
    grid.addEventListener("drop", (event) => {
      if (!state.draggingId) return;
      event.preventDefault();
      const id = state.draggingId;
      clearDropMarks();
      moveItem(id, state.items.length - 1);
      $("sort-key").value = "manual";
    });
  }

  bind();
  render();

  // Small hook so tests and screenshot tooling can drive the app without
  // scraping the DOM. Documented in README.md.
  window.imageToPdf = {
    browseTo,
    addPaths,
    state,
    render,
    exportPdf,
  };

  apiJson("/api/health")
    .then((health) => {
      state.root = health;
      if (!health.nativeFolderPicker) {
        $("btn-open-folder").title = "Native dialog unavailable — use Browse in app or Add images";
      }
    })
    .catch(() => {
      toast("Server not reachable. Is the app still running?", "error");
    });
})();
