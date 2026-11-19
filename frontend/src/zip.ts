// A zip archive written in memory with the "store" method (no compression),
// for sending several files in one upload. Only what that needs: no folders,
// no timestamps of the person's files, no ZIP64. Entry names are given by the
// caller (the e-mail upload uses neutral ones, so a file name never leaves the
// browser).

export interface ZipEntry {
    /** ASCII only: the name is written without the UTF-8 flag. */
    name: string;
    data: Uint8Array;
}

/** The limits of a zip without ZIP64: one 16-bit count and 32-bit sizes. */
const MAX_ENTRIES = 0xffff;
const MAX_BYTES = 0xffffffff;
// 1980-01-01 00:00:00, the earliest time a zip can say: the same for every entry.
const DOS_TIME = 0;
const DOS_DATE = (0 << 9) | (1 << 5) | 1;
/** The version that can read a stored entry (2.0). */
const VERSION = 20;

const LOCAL_HEADER = 30;
const CENTRAL_HEADER = 46;
const END_RECORD = 22;

const TABLE = (() => {
    const table = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
        let c = n;
        for (let k = 0; k < 8; k++)
            c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
        table[n] = c >>> 0;
    }
    return table;
})();

/** CRC-32 (IEEE 802.3, as zip uses it) of `data`, as an unsigned number. */
export function crc32(data: Uint8Array): number {
    let crc = 0xffffffff;
    for (let i = 0; i < data.length; i++)
        crc = TABLE[(crc ^ data[i]) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
}

function asciiName(name: string): Uint8Array {
    const bytes = new Uint8Array(name.length);
    for (let i = 0; i < name.length; i++) {
        const code = name.charCodeAt(i);
        if (code < 0x20 || code > 0x7e)
            throw new RangeError("A zip entry name must be plain ASCII.");
        bytes[i] = code;
    }
    return bytes;
}

/**
 * The size of the archive `storedZip` makes for entries with these data
 * lengths and name lengths, so that a bound can be checked before any file is
 * read into memory.
 */
export function storedZipSize(
    entries: { size: number; nameLength: number }[],
): number {
    return entries.reduce(
        (total, entry) =>
            total +
            LOCAL_HEADER +
            CENTRAL_HEADER +
            2 * entry.nameLength +
            entry.size,
        END_RECORD,
    );
}

/** The archive holding `entries` in order, every one stored uncompressed. */
export function storedZip(entries: ZipEntry[]): Uint8Array {
    if (entries.length > MAX_ENTRIES)
        throw new RangeError("Too many entries for a zip without ZIP64.");
    const names = entries.map((entry) => asciiName(entry.name));
    const total = storedZipSize(
        entries.map((entry, i) => ({
            size: entry.data.length,
            nameLength: names[i].length,
        })),
    );
    if (total > MAX_BYTES)
        throw new RangeError(
            "The archive is too large for a zip without ZIP64.",
        );
    const out = new Uint8Array(total);
    const view = new DataView(out.buffer);
    const offsets: number[] = [];
    const crcs: number[] = [];
    let at = 0;
    entries.forEach((entry, i) => {
        const crc = crc32(entry.data);
        crcs.push(crc);
        offsets.push(at);
        view.setUint32(at, 0x04034b50, true);
        view.setUint16(at + 4, VERSION, true);
        view.setUint16(at + 6, 0, true); // flags: none
        view.setUint16(at + 8, 0, true); // method 0: stored
        view.setUint16(at + 10, DOS_TIME, true);
        view.setUint16(at + 12, DOS_DATE, true);
        view.setUint32(at + 14, crc, true);
        view.setUint32(at + 18, entry.data.length, true); // compressed
        view.setUint32(at + 22, entry.data.length, true); // uncompressed
        view.setUint16(at + 26, names[i].length, true);
        view.setUint16(at + 28, 0, true); // no extra field
        out.set(names[i], at + LOCAL_HEADER);
        out.set(entry.data, at + LOCAL_HEADER + names[i].length);
        at += LOCAL_HEADER + names[i].length + entry.data.length;
    });
    const directory = at;
    entries.forEach((entry, i) => {
        view.setUint32(at, 0x02014b50, true);
        view.setUint16(at + 4, VERSION, true); // made by
        view.setUint16(at + 6, VERSION, true); // needed to read
        view.setUint16(at + 8, 0, true);
        view.setUint16(at + 10, 0, true);
        view.setUint16(at + 12, DOS_TIME, true);
        view.setUint16(at + 14, DOS_DATE, true);
        view.setUint32(at + 16, crcs[i], true);
        view.setUint32(at + 20, entry.data.length, true);
        view.setUint32(at + 24, entry.data.length, true);
        view.setUint16(at + 28, names[i].length, true);
        view.setUint16(at + 30, 0, true); // extra
        view.setUint16(at + 32, 0, true); // comment
        view.setUint16(at + 34, 0, true); // disk
        view.setUint16(at + 36, 0, true); // internal attributes
        view.setUint32(at + 38, 0, true); // external attributes
        view.setUint32(at + 42, offsets[i], true);
        out.set(names[i], at + CENTRAL_HEADER);
        at += CENTRAL_HEADER + names[i].length;
    });
    view.setUint32(at, 0x06054b50, true);
    view.setUint16(at + 4, 0, true); // this disk
    view.setUint16(at + 6, 0, true); // disk of the directory
    view.setUint16(at + 8, entries.length, true);
    view.setUint16(at + 10, entries.length, true);
    view.setUint32(at + 12, at - directory, true);
    view.setUint32(at + 16, directory, true);
    view.setUint16(at + 20, 0, true); // no comment
    return out;
}
