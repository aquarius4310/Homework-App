/* Homework grabber, by Stu.
   Runs on basised-dc.schoology.com when you click the "Get homework" bookmark.
   It only READS your own Schoology (the same pages you can see), then opens the
   shared homework page with the results. It never posts, submits, or changes
   anything on Schoology, and it never sees your password. */
(function () {
  const PAGE = "https://aquarius4310.github.io/Homework-Shared/";
  const TEST = /\b(quiz|test|exam|cfu|acc|accuracy|assessment|midterm|final|frq)\b/i;
  const SKIP = /\b(notes?|slides?|powerpoint|ppt|warm ?up|do ?now|spatial starter|exit ticket|survey|answer key|key|solutions?)\b/i;

  const get = async (u) => { const r = await fetch(u, { credentials: "include" }); if (!r.ok) throw new Error(u + " " + r.status); return r.text(); };
  const doc = (h) => new DOMParser().parseFromString(h, "text/html");
  const nid = (s) => ((String(s).match(/(\d{9,})(?!.*\d{9,})/) || [])[1] || "");
  const text = (html) => {
    const d = doc(String(html)
      .replace(/<br\s*\/?>/gi, "\n")
      .replace(/<\/(p|li|div|h\d|tr)>/gi, "\n")
      .replace(/<li[^>]*>/gi, "- "));
    return (d.body.textContent || "").replace(/ /g, " ").replace(/[ \t]+/g, " ").replace(/\n\s*\n+/g, "\n").trim();
  };
  const iso = (d) => { const z = (n) => String(n).padStart(2, "0"); return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`; };
  const today = iso(new Date());
  const parseDue = (s) => { if (!s) return ""; const d = new Date(String(s).replace(/ at .*/, "").replace(/^\w+, /, "")); return isNaN(d) ? "" : iso(d); };

  let box;
  const say = (msg) => {
    if (!box) {
      box = document.createElement("div");
      box.style.cssText = "position:fixed;z-index:2147483647;top:16px;right:16px;max-width:340px;background:#1b2230;color:#fff;font:14px/1.4 system-ui,sans-serif;padding:14px 16px;border-radius:10px;box-shadow:0 6px 24px rgba(0,0,0,.35)";
      document.body.appendChild(box);
    }
    box.textContent = msg;
  };

  async function grab() {
    if (!/schoology\.com$/.test(location.hostname)) throw new Error("Open Schoology first, then click the bookmark again.");
    say("Getting your homework. This takes about 30 seconds. Keep this tab open.");
    const nav = JSON.parse(await get("/iapi2/site-navigation/courses")).data.courses;
    const me = (document.querySelector('[data-sgy-sitenav="header-my-account-menu"] img, .user-picture img') || {}).alt || "";
    const courses = [];
    let n = 0;
    for (const c of nav) {
      n++; say(`Reading class ${n} of ${nav.length}: ${c.courseTitle}`);
      const course = { sec: String(c.nid), course: String(c.courseNid || ""), title: c.courseTitle, section: c.sectionTitle || "", teacher: "", posts: [], items: [] };
      // teacher posts (the weekly "CJ" and other updates), last 14 days
      try {
        const feed = doc(JSON.parse(await get(`/course/${c.nid}/feed?page=0`)).output || "");
        const authors = {};
        for (const li of [...feed.querySelectorAll("li[id^='edge-assoc-']")].slice(0, 6)) {
          const who = (li.querySelector(".update-sentence-inner a[href^='/user/'], a[href^='/user/']:not(:empty)") || {}).textContent || "";
          const when = (li.querySelector(".small.gray, .datetime") || {}).textContent || "";
          const at = new Date(when.replace(" at ", " "));
          if (!isNaN(at) && Date.now() - at > 14 * 864e5) continue;
          let body = li.querySelector(".update-body");
          let full = body ? text(body.innerHTML) : "";
          const more = [...li.querySelectorAll("a")].find((a) => /show_more/.test(a.getAttribute("href") || ""));
          if (more) { try { full = text(JSON.parse(await get(more.getAttribute("href"))).update || "") || full; } catch (e) {} }
          if (!full) continue;
          if (who.trim()) authors[who.trim()] = (authors[who.trim()] || 0) + 1;
          course.posts.push({ id: nid(li.id), at: isNaN(at) ? "" : at.toISOString(), by: who.trim(), text: full.slice(0, 4000) });
          if (course.posts.length >= 3) break;
        }
        course.teacher = Object.keys(authors).sort((a, b) => authors[b] - authors[a])[0] || "";
      } catch (e) { course.error = "posts: " + e.message; }
      // materials with a due date (assignments, packets, projects)
      try {
        const seen = new Set();
        const crawl = async (url, path, depth) => {
          const d = doc(await get(url));
          for (const tr of d.querySelectorAll("#folder-contents-table tr")) {
            const a = tr.querySelector(".item-title a") || tr.querySelector("a");
            if (!a) continue;
            const href = a.getAttribute("href") || "", name = a.textContent.replace(/\s+/g, " ").trim();
            if (tr.className.includes("folder") && href.includes("?f=")) { if (depth < 4) await crawl(href, path ? path + " / " + name : name, depth + 1); continue; }
            const info = tr.textContent.replace(/\s+/g, " ");
            const m = info.match(/Due (\w+, \w+ \d+, \d{4})(?: at ([\d:]+ [ap]m))?/);
            const id = nid(href);
            if (!m || !id || seen.has(id)) continue;
            const due = parseDue(m[1]);
            if (!due || due < today) continue;
            if (SKIP.test(name) && !TEST.test(name)) continue;
            seen.add(id);
            course.items.push({ id: "sgy-" + id, kind: TEST.test(name) ? "tests" : "hw", n: name.slice(0, 140), due, dueTime: m[2] || "", url: href, src: "materials" });
          }
        };
        await crawl(`/course/${c.nid}/materials`, "", 0);
      } catch (e) { course.error = (course.error ? course.error + "; " : "") + "materials: " + e.message; }
      courses.push(course);
    }
    // calendar: assignments and assessments in the next 60 days
    say("Reading your calendar");
    try {
      const calId = ((await fetch("/calendar", { credentials: "include" })).url.match(/calendar\/(\d+)/) || [])[1];
      if (calId) {
        const s = Math.floor(Date.now() / 1000) - 86400, e = s + 86400 * 60;
        const ym = new Date().toISOString().slice(0, 7);
        const ev = JSON.parse(await get(`/calendar/${calId}/${ym}?ajax=1&start=${s}&end=${e}`));
        for (const x of ev) {
          if (x.e_type === "folder" || /student hour/i.test(x.title)) continue;
          const href = (x.title.match(/href="([^"]+)"/) || [])[1] || "";
          const name = x.title.replace(/<[^>]+>/g, "").trim();
          const due = (x.start || "").slice(0, 10);
          if (!due || due < today) continue;
          const title = (x.content_title || "").replace(/:? ?: Section.*| : Section.*/, "").trim();
          const course = courses.find((c) => c.title === title) || courses.find((c) => title && title.startsWith(c.title));
          if (!course) continue;
          const id = "sgy-" + (nid(href) || x.id);
          if (course.items.some((i) => i.id === id)) continue;
          course.items.push({ id, kind: TEST.test(name) || x.e_type === "common-assessment" ? "tests" : "hw", n: name.slice(0, 140), due, dueTime: (x.start || "").slice(11, 16), url: href, src: "calendar" });
        }
      }
    } catch (e) { /* calendar is extra, skip on error */ }
    // personal: things Schoology says you have not turned in (never shared)
    say("Checking what you still need to turn in");
    const personal = [];
    for (const k of ["overdue", "upcoming"]) {
      try {
        const t = await get(`/home/${k}_submissions_ajax`);
        let h = t; try { h = JSON.parse(t).html || t; } catch (e) {}
        for (const ev of doc(h).querySelectorAll(".upcoming-event")) {
          const a = ev.querySelector("a"); if (!a) continue;
          const txt = ev.textContent.replace(/\s+/g, " ");
          const dueTxt = ((txt.match(/due (?:on )?(.+?)$/i) || [])[1] || "").trim();
          const href = a.getAttribute("href") || "";
          const secId = (href.match(/course\/(\d+)/) || [])[1] || "";
          personal.push({ id: "sgy-" + nid(href), n: a.textContent.trim().slice(0, 140), due: parseDue(dueTxt), late: k === "overdue", url: href, sec: secId });
        }
      } catch (e) {}
    }
    return { v: 1, at: new Date().toISOString(), who: me, courses, personal };
  }

  async function pack(obj) {
    const bytes = new TextEncoder().encode(JSON.stringify(obj));
    const cs = new Blob([bytes]).stream().pipeThrough(new CompressionStream("gzip"));
    const buf = new Uint8Array(await new Response(cs).arrayBuffer());
    let bin = ""; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  window.hwGrab = async function (opts) {
    opts = opts || {};
    try {
      const data = await grab();
      if (opts.redirect === false) { if (box) box.remove(); box = null; return data; }
      say("Done. Opening your homework page.");
      location.href = PAGE + "#d=" + (await pack(data));
    } catch (e) {
      say("Something went wrong: " + e.message + ". Try again, or tell Stu.");
      if (opts.redirect === false) throw e;
    }
  };
  if (!window.__hwGrabNoAuto) window.hwGrab({ redirect: true });
})();
