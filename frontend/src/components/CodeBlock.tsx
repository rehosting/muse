import { memo, useEffect, useState } from "react";
import { highlight } from "../util/highlight";
import { copyToClipboard } from "../util/shell";

interface Props {
  code: string;
  lang: string;
}

/** Syntax-highlighted code via shiki, with a header (language + copy button). */
function CodeBlock({ code, lang }: Props) {
  const [html, setHtml] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    highlight(code, lang).then((h) => {
      if (alive) setHtml(h);
    });
    return () => {
      alive = false;
    };
  }, [code, lang]);

  const onCopy = async () => {
    const ok = await copyToClipboard(code);
    setCopied(ok);
    setTimeout(() => setCopied(false), 1400);
  };

  return (
    <div className="codeblock">
      <div className="codeblock-head">
        <span className="codeblock-lang">{lang === "text" ? "" : lang}</span>
        <button className="codeblock-copy" onClick={onCopy} title="Copy code">
          {copied ? "✓ copied" : "copy"}
        </button>
      </div>
      {html ? (
        <div className="codeblock-body" dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <pre className="code codeblock-body">{code}</pre>
      )}
    </div>
  );
}

export default memo(CodeBlock);
