// Pulls the post and comments out of the page the operator is looking at.
//
// Injected on demand by background.js, never as a standing content script — the
// manifest asks for activeTab rather than permission on facebook.com, so this runs
// only on the tab whose capture button was pressed, only when it was pressed.
//
// It reads. It does not click, scroll, or dispatch events. Synthetic input from a
// content script carries isTrusted:false, which these platforms check, and faking it
// would need chrome.debugger — the automation surface Camoufox was chosen to avoid.
// Comments that have not been expanded are simply not captured; press "view more
// comments" yourself and capture again, and the second capture merges.
//
// Selectors drift. Every platform reshuffles its DOM, and when that happens this
// returns fewer comments rather than wrong ones: each extractor requires a non-empty
// text before it emits anything.

function ankedoExtract() {
  const host = location.hostname.replace(/^www\./, "");

  const platform = host.includes("facebook") ? "facebook"
    : host.includes("instagram") ? "instagram"
    : host.includes("tiktok") ? "tiktok"
    : null;

  if (!platform) {
    return { error: `Not a supported platform: ${host}` };
  }

  const clean = (value) => (value || "").replace(/\s+/g, " ").trim();

  // A stable id per comment so a re-capture merges instead of duplicating. The
  // platforms do not expose one in the DOM we can see, so hash the author and text:
  // the same comment yields the same id, a different one does not.
  const idFor = (seed) => {
    let hash = 5381;
    for (let i = 0; i < seed.length; i++) {
      hash = ((hash << 5) + hash + seed.charCodeAt(i)) >>> 0;
    }
    return `dom-${hash.toString(36)}`;
  };

  const postIdFromUrl = () => {
    const url = new URL(location.href);
    const known = ["story_fbid", "fbid", "post_id", "v"];
    for (const key of known) {
      const value = url.searchParams.get(key);
      if (value) return value;
    }
    // /p/<id>/, /reel/<id>/, /posts/<id>, /video/<id>
    const match = url.pathname.match(/\/(?:p|reel|posts|video|photo)\/([^/?#]+)/);
    if (match) return match[1];
    return url.pathname.replace(/\/+$/, "").split("/").filter(Boolean).pop() || url.pathname;
  };

  const SELECTORS = {
    facebook: {
      article: 'div[role="article"]',
      body: '[data-ad-preview="message"], [data-ad-comet-preview="message"]',
      author: 'h2 a, h3 a, strong a',
    },
    instagram: {
      article: "article",
      body: "h1, li h1",
      author: "header a",
    },
    tiktok: {
      article: '[data-e2e="browse-video"], [data-e2e="video-detail"]',
      body: '[data-e2e="browse-video-desc"], [data-e2e="video-desc"]',
      author: '[data-e2e="browse-username"], [data-e2e="video-author-uniqueid"]',
    },
  }[platform];

  const root = document.querySelector(SELECTORS.article) || document.body;
  const bodyNode = root.querySelector(SELECTORS.body);
  const authorNode = root.querySelector(SELECTORS.author);

  // Comments differ enough per platform that one selector will not do. Each returns
  // {author, text} pairs; anything without text is dropped.
  const comments = [];
  const push = (author, text) => {
    const body = clean(text);
    if (!body) return;
    const name = clean(author) || null;
    comments.push({
      platform_comment_id: idFor(`${name}|${body}`),
      text: body,
      author_name: name,
    });
  };

  if (platform === "facebook") {
    // Comment articles are nested inside the post article; the outermost one is the
    // post itself, so skip it.
    const articles = Array.from(document.querySelectorAll('div[role="article"]'));
    for (const node of articles) {
      if (node === root) continue;
      const author = node.querySelector("a[role='link'] span, strong span, a span");
      const text = node.querySelector('div[dir="auto"]:not([role])');
      push(author?.textContent, text?.textContent);
    }
  } else if (platform === "instagram") {
    for (const node of document.querySelectorAll("ul li")) {
      const author = node.querySelector("a");
      const text = node.querySelector("span[dir], span");
      if (author && text) push(author.textContent, text.textContent);
    }
  } else {
    for (const node of document.querySelectorAll('[data-e2e="comment-level-1"], [data-e2e="comment-item"]')) {
      const author = node.querySelector('[data-e2e="comment-username-1"], a');
      const text = node.querySelector('[data-e2e="comment-level-1"] p, p, span');
      push(author?.textContent, text?.textContent);
    }
  }

  const media = Array.from(root.querySelectorAll("img[src]"))
    .map((img) => img.src)
    .filter((src) => src.startsWith("http") && !src.includes("/emoji"))
    .slice(0, 20);

  const contentText = clean(bodyNode?.textContent) || clean(root.innerText).slice(0, 4000);

  return {
    platform,
    url: location.href.split("#")[0],
    platform_post_id: postIdFromUrl(),
    content_text: contentText || null,
    author_name: clean(authorNode?.textContent) || null,
    author_handle: clean(authorNode?.getAttribute?.("href"))?.split("/").filter(Boolean).pop() || null,
    media_urls: media,
    comments,
  };
}
