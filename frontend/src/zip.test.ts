import { describe, expect, it } from "vitest";
import { crc32, storedZip, storedZipSize } from "./zip";

const bytes = (text: string) => new TextEncoder().encode(text);

/** A reader written from the zip specification, independent of the writer. */
function read(archive: Uint8Array) {
    const view = new DataView(
        archive.buffer,
        archive.byteOffset,
        archive.byteLength,
    );
    // The end record is the last 22 bytes: this writer adds no comment.
    const end = archive.length - 22;
    expect(view.getUint32(end, true)).toBe(0x06054b50);
    const count = view.getUint16(end + 10, true);
    expect(view.getUint16(end + 8, true)).toBe(count);
    const size = view.getUint32(end + 12, true);
    const start = view.getUint32(end + 16, true);
    expect(start + size).toBe(end);
    const entries: {
        name: string;
        crc: number;
        data: Uint8Array;
        method: number;
    }[] = [];
    let at = start;
    for (let i = 0; i < count; i++) {
        expect(view.getUint32(at, true)).toBe(0x02014b50);
        const method = view.getUint16(at + 10, true);
        const crc = view.getUint32(at + 16, true);
        const compressed = view.getUint32(at + 20, true);
        const plain = view.getUint32(at + 24, true);
        const nameLength = view.getUint16(at + 28, true);
        const extra = view.getUint16(at + 30, true);
        const comment = view.getUint16(at + 32, true);
        const local = view.getUint32(at + 42, true);
        const name = new TextDecoder().decode(
            archive.slice(at + 46, at + 46 + nameLength),
        );
        // The local header repeats what the directory says.
        expect(view.getUint32(local, true)).toBe(0x04034b50);
        expect(view.getUint32(local + 14, true)).toBe(crc);
        expect(view.getUint32(local + 18, true)).toBe(compressed);
        expect(view.getUint32(local + 22, true)).toBe(plain);
        expect(view.getUint16(local + 26, true)).toBe(nameLength);
        const dataAt =
            local + 30 + nameLength + view.getUint16(local + 28, true);
        expect(compressed).toBe(plain);
        entries.push({
            name,
            crc,
            method,
            data: archive.slice(dataAt, dataAt + plain),
        });
        at += 46 + nameLength + extra + comment;
    }
    expect(at).toBe(end);
    return entries;
}

describe("crc32", () => {
    it("matches the known check values", () => {
        // The standard check value of CRC-32/ISO-HDLC.
        expect(crc32(bytes("123456789"))).toBe(0xcbf43926);
        expect(crc32(new Uint8Array())).toBe(0);
        expect(crc32(bytes("a"))).toBe(0xe8b7be43);
        expect(
            crc32(bytes("The quick brown fox jumps over the lazy dog")),
        ).toBe(0x414fa339);
        // Bytes above 127 are bytes, not characters.
        expect(crc32(new Uint8Array([0xff, 0xff, 0xff, 0xff]))).toBe(
            0xffffffff,
        );
        expect(crc32(new Uint8Array([0, 0, 0, 0]))).toBe(0x2144df1c);
    });
});

describe("storedZip", () => {
    it("writes an archive whose directory, headers, checksums and data agree", () => {
        const files = [
            { name: "0001.eml", data: bytes("Subject: one\r\n\r\nHello") },
            { name: "0002.eml", data: new Uint8Array([0, 255, 128, 10, 13]) },
            { name: "0003.eml", data: new Uint8Array() },
        ];
        const archive = storedZip(files);
        expect(archive.length).toBe(
            storedZipSize(
                files.map((f) => ({ size: f.data.length, nameLength: 8 })),
            ),
        );
        // It starts with a local header and ends with the end record.
        expect(Array.from(archive.slice(0, 4))).toEqual([
            0x50, 0x4b, 0x03, 0x04,
        ]);
        const found = read(archive);
        expect(found.map((e) => e.name)).toEqual(files.map((f) => f.name));
        found.forEach((entry, i) => {
            expect(entry.method).toBe(0);
            expect(Array.from(entry.data)).toEqual(Array.from(files[i].data));
            expect(entry.crc).toBe(crc32(files[i].data));
        });
    });
    it("carries no time of the files and the same bytes every time", () => {
        const once = storedZip([{ name: "0001.eml", data: bytes("x") }]);
        const again = storedZip([{ name: "0001.eml", data: bytes("x") }]);
        expect(once).toEqual(again);
        const view = new DataView(once.buffer);
        // 1980-01-01 00:00:00
        expect(view.getUint16(10, true)).toBe(0);
        expect(view.getUint16(12, true)).toBe(0x21);
    });
    it("writes an empty archive that is only an end record", () => {
        const archive = storedZip([]);
        expect(archive.length).toBe(22);
        expect(read(archive)).toEqual([]);
    });
    it("refuses a name that is not plain ASCII and more entries than a zip counts", () => {
        expect(() =>
            storedZip([{ name: "почта.eml", data: new Uint8Array() }]),
        ).toThrow(RangeError);
        expect(() =>
            storedZip([{ name: "a\u0000.eml", data: new Uint8Array() }]),
        ).toThrow(RangeError);
        expect(() =>
            storedZip(
                Array.from({ length: 0x10000 }, () => ({
                    name: "a",
                    data: new Uint8Array(),
                })),
            ),
        ).toThrow(RangeError);
    });
});
