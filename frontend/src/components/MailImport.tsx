import { useRef, useState } from "react";
import { Mail } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MailCounts } from "../api";
import { Button } from "./ui/button";

/**
 * The second way into the first step: a table built from e-mail files. It says
 * plainly, before anything is chosen, what is done with the files. The files
 * are packed in the browser (see `packMails`) and read by this application;
 * the page offers this only where the server says tables can be built from
 * e-mails.
 */
export function MailImport({
    disabled,
    onFiles,
}: {
    disabled: boolean;
    onFiles: (files: File[]) => void;
}) {
    const { t } = useTranslation();
    const input = useRef<HTMLInputElement>(null);
    const [dragging, setDragging] = useState(false);
    return (
        <section
            className={`source-mail ${dragging ? "is-dragging" : ""}`}
            aria-label={t("mail.title")}
            data-testid="mail-import"
            onDragOver={(e) => {
                e.preventDefault();
                if (!disabled) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                if (!disabled) onFiles([...e.dataTransfer.files]);
            }}
        >
            <div className="mail-heading">
                <Mail aria-hidden="true" />
                <strong>{t("mail.title")}</strong>
            </div>
            <p className="muted">{t("mail.hint")}</p>
            <ul className="mail-privacy" data-testid="mail-privacy">
                <li>{t("mail.privacy.where")}</li>
                <li>{t("mail.privacy.masked")}</li>
            </ul>
            <input
                ref={input}
                type="file"
                multiple
                accept=".eml,.zip"
                className="sr-only"
                aria-label={t("mail.input")}
                disabled={disabled}
                onChange={(e) => {
                    onFiles([...(e.target.files ?? [])]);
                    e.target.value = "";
                }}
            />
            <div className="mail-actions">
                <Button
                    variant="outline"
                    size="sm"
                    disabled={disabled}
                    onClick={() => input.current?.click()}
                >
                    {t("mail.choose")}
                </Button>
                <span className="muted">{t("mail.dropHere")}</span>
            </div>
        </section>
    );
}

/** What was read, skipped and cut, and how many masks of each kind were applied. */
export function MailCountsView({ counts }: { counts: MailCounts }) {
    const { t } = useTranslation();
    return (
        <div className="mail-counts notice" data-testid="mail-counts">
            <strong>{t("mail.counts.title")}</strong>
            <ul>
                <li data-count="read">
                    {t("mail.counts.read", { count: counts.read })}
                </li>
                <li data-count="skipped">
                    {t("mail.counts.skipped", { count: counts.skipped })}
                </li>
                <li data-count="truncated">
                    {t("mail.counts.truncated", { count: counts.truncated })}
                </li>
                <li data-count="masked">
                    {t("mail.counts.masked", {
                        card: counts.masked.card,
                        phone: counts.masked.phone,
                        me: counts.masked.me,
                    })}
                </li>
            </ul>
            <p className="muted">{t("mail.counts.after")}</p>
        </div>
    );
}
