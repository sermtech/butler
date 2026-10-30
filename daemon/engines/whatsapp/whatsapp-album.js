/**
 * RV-8 (F-15): the photos and videos of ONE album become ONE item.
 *
 * WhatsApp sends an album as a parent `albumMessage` (its announced size) and
 * one message per photo or video, each naming the parent through
 * `messageContextInfo.messageAssociation` (whatsapp-inbound.js
 * albumParentOf). Routed one by one, each photo was its own owner turn.
 *
 * The gate holds an album's children until no new one arrived for `settleMs`
 * (3 s), or until the announced number is in, then hands on ONE item: the
 * first child's fields, `media` = the first file (what a daemon that knows
 * nothing of albums still answers), `album` = every file in order, `text` =
 * the captions joined. Items of the SAME chat that arrive while an album is
 * open wait behind it, so a question typed after the photos is never answered
 * before them. Other chats are not held.
 */
export const ALBUM_SETTLE_MS = 3000;
const SIZES_MAX = 200;

/**
 * @param {object} p
 * @param {(item: object, ids: string[]) => void} p.emit - one finished item and
 *   the message ids it stands for
 * @param {number} [p.settleMs]
 */
export function createAlbumGate({ emit, settleMs = ALBUM_SETTLE_MS }) {
  /** jid -> slots in arrival order: {item} or {album: {...}} */
  const chats = new Map();
  /** parent id -> announced size; the parent may come before or after. */
  const sizes = new Map();

  function merged(album) {
    const [first] = album.items;
    const captions = album.items.map((i) => i.text).filter(Boolean);
    return {
      ...first,
      text: captions.join('\n'),
      media: first.media,
      album: album.items.map((i) => i.media),
      albumId: album.parent,
    };
  }

  function drain(jid) {
    const q = chats.get(jid);
    while (q && q.length) {
      const slot = q[0];
      if (slot.album && !slot.album.closed) return;
      q.shift();
      if (slot.album) emit(merged(slot.album), slot.album.items.map((i) => i.id));
      else emit(slot.item, slot.item.id ? [slot.item.id] : []);
    }
    chats.delete(jid);
  }

  function close(jid, album) {
    if (album.closed) return;
    clearTimeout(album.timer);
    album.closed = true;
    drain(jid);
  }

  function openAlbum(jid, parent) {
    for (const s of chats.get(jid) || []) {
      if (s.album && s.album.parent === parent && !s.album.closed) return s.album;
    }
    return null;
  }

  return {
    /** The parent `albumMessage` arrived: remember how many files it announced. */
    size(jid, parent, n) {
      sizes.set(parent, n);
      if (sizes.size > SIZES_MAX) sizes.delete(sizes.keys().next().value);
      const album = openAlbum(jid, parent);
      if (album && album.items.length >= n) close(jid, album);
    },

    /** One routed item; `parent` is its album's id, or null for anything else. */
    offer(item, parent = null) {
      const { jid } = item;
      if (!parent) {
        if (!chats.has(jid)) return emit(item, item.id ? [item.id] : []);
        chats.get(jid).push({ item });
        return undefined;
      }
      if (!chats.has(jid)) chats.set(jid, []);
      let album = openAlbum(jid, parent);
      if (!album) {
        album = { parent, items: [], closed: false, timer: null };
        chats.get(jid).push({ album });
      }
      // A redelivery inside the window is the same file, not another one.
      if (album.items.some((i) => i.id === item.id)) return undefined;
      album.items.push(item);
      clearTimeout(album.timer);
      const n = sizes.get(parent);
      if (n && album.items.length >= n) return close(jid, album);
      album.timer = setTimeout(() => close(jid, album), settleMs);
      album.timer.unref?.();
      return undefined;
    },

    /** Hand on everything held now (the socket is closing). */
    flush() {
      for (const [jid, q] of [...chats]) {
        for (const s of q) if (s.album) { clearTimeout(s.album.timer); s.album.closed = true; }
        drain(jid);
      }
    },
  };
}
