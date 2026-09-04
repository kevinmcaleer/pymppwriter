/**
 * Microsoft Compound File Binary (OLE2) containers — read and write.
 *
 * Enough of [MS-CFB] v3 (512-byte sectors, 64-byte mini sectors, 4096-byte
 * mini-stream cutoff, DIFAT chains) to produce files Microsoft Project, Apache
 * POI and olefile read, and to read the ones they write. Written from the
 * public [MS-CFB] specification; a direct port of the Python implementation, so
 * both produce the same bytes.
 */

export const FREESECT = 0xffffffff;
export const ENDOFCHAIN = 0xfffffffe;
export const FATSECT = 0xfffffffd;
export const DIFSECT = 0xfffffffc;
export const NOSTREAM = 0xffffffff;

export const SECTOR = 512;
export const MINI_SECTOR = 64;
export const MINI_CUTOFF = 4096;
const FAT_PER_SECTOR = SECTOR / 4;
const DIFAT_PER_SECTOR = FAT_PER_SECTOR - 1; // the last dword chains onward

export const TYPE_STORAGE = 1;
export const TYPE_STREAM = 2;
export const TYPE_ROOT = 5;

const MAGIC = Uint8Array.from([0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]);

/** A storage node: an ordered tree of child storages and streams. */
export class Storage {
  /** Insertion-ordered, and a Map because JS objects reorder integer-like keys. */
  readonly children = new Map<string, Storage | Uint8Array>();

  addStream(name: string, data: Uint8Array): void {
    this.children.set(name, data);
  }

  addStorage(name: string): Storage {
    const existing = this.children.get(name);
    if (existing instanceof Storage) return existing;
    const s = new Storage();
    this.children.set(name, s);
    return s;
  }

  /** `set("a/b/c", data)` creating intermediate storages. */
  set(path: string, data: Uint8Array): void {
    const parts = path.split("/");
    let s: Storage = this;
    for (const p of parts.slice(0, -1)) s = s.addStorage(p);
    s.addStream(parts[parts.length - 1]!, data);
  }

  /** The stream at `path`, or undefined when it is absent or a storage. */
  get(path: string): Uint8Array | undefined {
    const node = this.node(path);
    return node instanceof Uint8Array ? node : undefined;
  }

  storage(path: string): Storage {
    let s: Storage = this;
    for (const p of path.split("/")) s = s.addStorage(p);
    return s;
  }

  private node(path: string): Storage | Uint8Array | undefined {
    let cur: Storage | Uint8Array | undefined = this;
    for (const p of path.split("/")) {
      if (!(cur instanceof Storage)) return undefined;
      cur = cur.children.get(p);
    }
    return cur;
  }

  /** Every stream path in the tree, depth first. */
  paths(prefix = ""): string[] {
    const out: string[] = [];
    for (const [name, val] of this.children) {
      const p = prefix ? `${prefix}/${name}` : name;
      if (val instanceof Storage) out.push(...val.paths(p));
      else out.push(p);
    }
    return out;
  }
}

class Entry {
  child = NOSTREAM;
  left = NOSTREAM;
  right = NOSTREAM;
  start = ENDOFCHAIN;
  size = 0;
  index = -1;
  clsid = new Uint8Array(16);
  name: string;
  etype: number;
  data: Uint8Array | undefined;

  // plain assignment, not parameter properties: Node runs this file by
  // stripping types, which only supports erasable syntax
  constructor(name: string, etype: number, data?: Uint8Array) {
    this.name = name;
    this.etype = etype;
    this.data = data;
  }
}

function utf16le(name: string): Uint8Array {
  const out = new Uint8Array(name.length * 2);
  const view = new DataView(out.buffer);
  for (let i = 0; i < name.length; i++) view.setUint16(i * 2, name.charCodeAt(i), true);
  return out;
}

function fromUtf16le(bytes: Uint8Array): string {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let s = "";
  for (let i = 0; i + 1 < bytes.byteLength; i += 2) s += String.fromCharCode(view.getUint16(i, true));
  return s;
}

/**
 * [MS-CFB] 2.6.4 ordering: by UTF-16 name length first, then upper-cased code
 * units. Exported because getting it wrong makes readers lose entries.
 */
export function compareNames(a: string, b: string): number {
  if (a.length !== b.length) return a.length - b.length;
  const ua = a.toUpperCase();
  const ub = b.toUpperCase();
  for (let i = 0; i < ua.length; i++) {
    const d = ua.charCodeAt(i) - ub.charCodeAt(i);
    if (d !== 0) return d;
  }
  return 0;
}

function buildTree(entries: Entry[]): number {
  if (entries.length === 0) return NOSTREAM;
  const mid = Math.floor(entries.length / 2);
  const node = entries[mid]!;
  node.left = buildTree(entries.slice(0, mid));
  node.right = buildTree(entries.slice(mid + 1));
  return node.index;
}

function pad(data: Uint8Array, unit: number): Uint8Array {
  const rem = data.length % unit;
  if (rem === 0) return data;
  const out = new Uint8Array(data.length + (unit - rem));
  out.set(data);
  return out;
}

function dirEntry(e: Entry | null): Uint8Array {
  const buf = new Uint8Array(128);
  const view = new DataView(buf.buffer);
  if (e === null) {
    view.setUint32(68, NOSTREAM, true);
    view.setUint32(72, NOSTREAM, true);
    view.setUint32(76, NOSTREAM, true);
    return buf;
  }
  const name = utf16le(e.name);
  if (name.length + 2 > 64) throw new Error(`name too long: ${e.name}`);
  buf.set(name, 0);
  view.setUint16(64, name.length + 2, true); // includes the terminating NUL
  buf[66] = e.etype;
  buf[67] = 1; // colour: black
  view.setUint32(68, e.left, true);
  view.setUint32(72, e.right, true);
  view.setUint32(76, e.child, true);
  buf.set(e.clsid, 80);
  const start = e.start !== ENDOFCHAIN ? e.start : e.etype !== TYPE_STREAM ? 0 : ENDOFCHAIN;
  view.setUint32(116, start, true);
  view.setBigUint64(120, BigInt(e.size), true);
  return buf;
}

/** Serialise a storage tree into a compound file. */
export function writeCfb(root: Storage, rootClsid = new Uint8Array(16)): Uint8Array {
  const entries: Entry[] = [];
  const rootEntry = new Entry("Root Entry", TYPE_ROOT);
  rootEntry.clsid = rootClsid;
  entries.push(rootEntry);

  const flatten = (storage: Storage, parent: Entry): void => {
    const kids: Entry[] = [];
    for (const [name, val] of storage.children) {
      const e =
        val instanceof Storage ? new Entry(name, TYPE_STORAGE) : new Entry(name, TYPE_STREAM, val);
      entries.push(e);
      e.index = entries.length - 1;
      kids.push(e);
    }
    kids.sort((a, b) => compareNames(a.name, b.name));
    parent.child = buildTree(kids);
    for (const e of kids) {
      if (e.etype === TYPE_STORAGE) flatten(storage.children.get(e.name) as Storage, e);
    }
  };
  rootEntry.index = 0;
  flatten(root, rootEntry);
  entries.forEach((e, i) => (e.index = i));

  // mini stream: every stream below the cutoff
  const miniParts: Uint8Array[] = [];
  const minifat: number[] = [];
  let miniLen = 0;
  for (const e of entries) {
    if (e.etype === TYPE_STREAM && e.data && e.data.length > 0 && e.data.length < MINI_CUTOFF) {
      e.size = e.data.length;
      e.start = minifat.length;
      const padded = pad(e.data, MINI_SECTOR);
      const n = padded.length / MINI_SECTOR;
      miniParts.push(padded);
      miniLen += padded.length;
      for (let i = 1; i < n; i++) minifat.push(minifat.length + 1);
      minifat.push(ENDOFCHAIN);
    }
  }
  const miniStream = new Uint8Array(miniLen);
  {
    let off = 0;
    for (const p of miniParts) {
      miniStream.set(p, off);
      off += p.length;
    }
  }
  rootEntry.size = miniStream.length;

  const sectors: Uint8Array[] = [];
  const fat: number[] = [];
  const addChain = (data: Uint8Array): number => {
    const padded = pad(data, SECTOR);
    const n = padded.length / SECTOR;
    if (n === 0) return ENDOFCHAIN;
    const first = sectors.length;
    for (let i = 0; i < n; i++) {
      sectors.push(padded.subarray(i * SECTOR, (i + 1) * SECTOR));
      fat.push(i < n - 1 ? first + i + 1 : ENDOFCHAIN);
    }
    return first;
  };

  for (const e of entries) {
    if (e.etype === TYPE_STREAM && e.data && e.data.length >= MINI_CUTOFF) {
      e.size = e.data.length;
      e.start = addChain(e.data);
    }
  }
  rootEntry.start = miniStream.length ? addChain(miniStream) : ENDOFCHAIN;

  let minifatStart = ENDOFCHAIN;
  let minifatCount = 0;
  if (minifat.length) {
    const raw = pad(new Uint8Array(minifat.length * 4), SECTOR);
    const view = new DataView(raw.buffer);
    minifat.forEach((v, i) => view.setUint32(i * 4, v, true));
    raw.fill(0xff, minifat.length * 4); // unused slots are FREESECT
    minifatStart = addChain(raw);
    minifatCount = raw.length / SECTOR;
  }

  // directory, padded out with valid empty entries
  const dirEntries: Uint8Array[] = entries.map((e) => dirEntry(e));
  const slotBytes = pad(new Uint8Array(dirEntries.length * 128), SECTOR).length;
  while (dirEntries.length * 128 < slotBytes) dirEntries.push(dirEntry(null));
  const dirRaw = new Uint8Array(dirEntries.length * 128);
  dirEntries.forEach((d, i) => dirRaw.set(d, i * 128));
  const dirStart = addChain(dirRaw);

  // FAT and DIFAT sectors need FAT entries of their own, so iterate to a fixpoint
  const nData = sectors.length;
  let nFat = 0;
  let nDifat = 0;
  for (;;) {
    const neededFat = Math.ceil((nData + nFat + nDifat) / FAT_PER_SECTOR);
    const neededDifat = Math.ceil(Math.max(0, neededFat - 109) / DIFAT_PER_SECTOR);
    if (neededFat === nFat && neededDifat === nDifat) break;
    nFat = neededFat;
    nDifat = neededDifat;
  }

  const fatFull = [...fat];
  for (let i = 0; i < nFat; i++) fatFull.push(FATSECT);
  for (let i = 0; i < nDifat; i++) fatFull.push(DIFSECT);
  while (fatFull.length < nFat * FAT_PER_SECTOR) fatFull.push(FREESECT);
  const fatRaw = new Uint8Array(fatFull.length * 4);
  {
    const view = new DataView(fatRaw.buffer);
    fatFull.forEach((v, i) => view.setUint32(i * 4, v, true));
  }
  const fatSectorIds = Array.from({ length: nFat }, (_, i) => nData + i);
  const difatSectorIds = Array.from({ length: nDifat }, (_, i) => nData + nFat + i);
  for (let i = 0; i < nFat; i++) sectors.push(fatRaw.subarray(i * SECTOR, (i + 1) * SECTOR));
  for (let i = 0; i < nDifat; i++) {
    const chunk = fatSectorIds.slice(109 + i * DIFAT_PER_SECTOR, 109 + (i + 1) * DIFAT_PER_SECTOR);
    const sector = new Uint8Array(SECTOR);
    const view = new DataView(sector.buffer);
    for (let j = 0; j < DIFAT_PER_SECTOR; j++) view.setUint32(j * 4, chunk[j] ?? FREESECT, true);
    view.setUint32(SECTOR - 4, i + 1 < nDifat ? difatSectorIds[i + 1]! : ENDOFCHAIN, true);
    sectors.push(sector);
  }

  const header = new Uint8Array(SECTOR);
  const hv = new DataView(header.buffer);
  header.set(MAGIC, 0);
  hv.setUint16(24, 0x003e, true); // minor version
  hv.setUint16(26, 0x0003, true); // major version 3
  hv.setUint16(28, 0xfffe, true); // little endian
  hv.setUint16(30, 9, true); // 512-byte sectors
  hv.setUint16(32, 6, true); // 64-byte mini sectors
  hv.setUint32(40, 0, true); // directory sector count (v3: 0)
  hv.setUint32(44, nFat, true);
  hv.setUint32(48, dirStart, true);
  hv.setUint32(56, MINI_CUTOFF, true);
  hv.setUint32(60, minifatStart, true);
  hv.setUint32(64, minifatCount, true);
  hv.setUint32(68, nDifat ? difatSectorIds[0]! : ENDOFCHAIN, true);
  hv.setUint32(72, nDifat, true);
  for (let i = 0; i < 109; i++) hv.setUint32(76 + i * 4, fatSectorIds[i] ?? FREESECT, true);

  const out = new Uint8Array(SECTOR + sectors.length * SECTOR);
  out.set(header, 0);
  sectors.forEach((s, i) => out.set(s, SECTOR + i * SECTOR));
  return out;
}

/** Parse a compound file into a storage tree. */
export function readCfb(bytes: Uint8Array): Storage {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let i = 0; i < MAGIC.length; i++) {
    if (bytes[i] !== MAGIC[i]) throw new Error("not a compound file (bad magic)");
  }
  const sectorSize = 1 << view.getUint16(30, true);
  const miniSectorSize = 1 << view.getUint16(32, true);
  const nFat = view.getUint32(44, true);
  const dirStart = view.getUint32(48, true);
  const miniCutoff = view.getUint32(56, true);
  const minifatStart = view.getUint32(60, true);
  const nDifat = view.getUint32(72, true);
  let difatStart = view.getUint32(68, true);

  const sectorOffset = (id: number) => sectorSize + id * sectorSize;
  const readSector = (id: number) => bytes.subarray(sectorOffset(id), sectorOffset(id) + sectorSize);

  // the DIFAT lists the FAT sectors: 109 in the header, the rest chained
  const fatSectorIds: number[] = [];
  for (let i = 0; i < Math.min(nFat, 109); i++) fatSectorIds.push(view.getUint32(76 + i * 4, true));
  for (let n = 0; n < nDifat && difatStart < FATSECT; n++) {
    const sec = readSector(difatStart);
    const sv = new DataView(sec.buffer, sec.byteOffset, sec.byteLength);
    for (let i = 0; i < sectorSize / 4 - 1; i++) {
      const id = sv.getUint32(i * 4, true);
      if (id < FATSECT) fatSectorIds.push(id);
    }
    difatStart = sv.getUint32(sectorSize - 4, true);
  }

  const fat: number[] = [];
  for (const id of fatSectorIds) {
    const sec = readSector(id);
    const sv = new DataView(sec.buffer, sec.byteOffset, sec.byteLength);
    for (let i = 0; i < sectorSize / 4; i++) fat.push(sv.getUint32(i * 4, true));
  }

  const chain = (start: number): number[] => {
    const out: number[] = [];
    let s = start;
    const seen = new Set<number>();
    while (s < FATSECT && !seen.has(s)) {
      seen.add(s);
      out.push(s);
      s = fat[s] ?? ENDOFCHAIN;
    }
    return out;
  };
  const readChain = (start: number, size?: number): Uint8Array => {
    const ids = chain(start);
    const out = new Uint8Array(ids.length * sectorSize);
    ids.forEach((id, i) => out.set(readSector(id), i * sectorSize));
    return size === undefined ? out : out.subarray(0, size);
  };

  const dirData = readChain(dirStart);
  const nEntries = Math.floor(dirData.length / 128);
  type Raw = { name: string; etype: number; child: number; left: number; right: number; start: number; size: number };
  const raw: Raw[] = [];
  for (let i = 0; i < nEntries; i++) {
    const e = dirData.subarray(i * 128, (i + 1) * 128);
    const ev = new DataView(e.buffer, e.byteOffset, e.byteLength);
    const nameLen = ev.getUint16(64, true);
    raw.push({
      name: nameLen > 2 ? fromUtf16le(e.subarray(0, nameLen - 2)) : "",
      etype: e[66]!,
      child: ev.getUint32(76, true),
      left: ev.getUint32(68, true),
      right: ev.getUint32(72, true),
      start: ev.getUint32(116, true),
      size: Number(ev.getBigUint64(120, true)),
    });
  }

  const rootRaw = raw[0];
  if (!rootRaw) throw new Error("compound file has no root entry");
  const miniStream = rootRaw.start < FATSECT ? readChain(rootRaw.start, rootRaw.size) : new Uint8Array(0);
  const minifat: number[] = [];
  if (minifatStart < FATSECT) {
    const mf = readChain(minifatStart);
    const mv = new DataView(mf.buffer, mf.byteOffset, mf.byteLength);
    for (let i = 0; i < mf.length / 4; i++) minifat.push(mv.getUint32(i * 4, true));
  }
  const readMini = (start: number, size: number): Uint8Array => {
    const out = new Uint8Array(size);
    let s = start;
    let off = 0;
    const seen = new Set<number>();
    while (s < FATSECT && off < size && !seen.has(s)) {
      seen.add(s);
      const take = Math.min(miniSectorSize, size - off);
      out.set(miniStream.subarray(s * miniSectorSize, s * miniSectorSize + take), off);
      off += take;
      s = minifat[s] ?? ENDOFCHAIN;
    }
    return out;
  };

  const root = new Storage();
  const walk = (index: number, into: Storage): void => {
    if (index === NOSTREAM || index >= raw.length) return;
    const e = raw[index]!;
    walk(e.left, into);
    if (e.etype === TYPE_STORAGE) {
      walk(e.child, into.addStorage(e.name));
    } else if (e.etype === TYPE_STREAM) {
      const data =
        e.size === 0
          ? new Uint8Array(0)
          : e.size < miniCutoff
            ? readMini(e.start, e.size)
            : readChain(e.start, e.size);
      into.addStream(e.name, data);
    }
    walk(e.right, into);
  };
  walk(rootRaw.child, root);
  sortChildren(root);
  return root;
}

/**
 * Order every storage's children by name.
 *
 * The directory tree's shape is not semantic — two readers can walk the same
 * file into differently ordered trees — but the writer emits entries in
 * insertion order, so the order decides the output bytes. Canonicalising here
 * keeps this implementation and the Python one producing identical files from
 * identical input, which is what the parity tests check.
 */
function sortChildren(node: Storage): void {
  const sorted = [...node.children.entries()].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  node.children.clear();
  for (const [name, val] of sorted) {
    node.children.set(name, val);
    if (val instanceof Storage) sortChildren(val);
  }
}
