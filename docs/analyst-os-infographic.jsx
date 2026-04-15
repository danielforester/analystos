import { useState, useEffect } from "react";

const colors = {
  bg: "#0a0e17",
  surface: "#111827",
  surfaceLight: "#1a2235",
  border: "#1e293b",
  accent: "#22d3ee",
  accentDim: "rgba(34,211,238,0.12)",
  orange: "#f59e0b",
  orangeDim: "rgba(245,158,11,0.12)",
  green: "#34d399",
  greenDim: "rgba(52,211,153,0.12)",
  violet: "#a78bfa",
  violetDim: "rgba(167,139,250,0.12)",
  red: "#f87171",
  redDim: "rgba(248,113,113,0.12)",
  text: "#e2e8f0",
  textDim: "#94a3b8",
  textMuted: "#64748b",
};

const font = `'IBM Plex Mono', 'Fira Code', 'JetBrains Mono', monospace`;
const sansFont = `'DM Sans', 'Segoe UI', sans-serif`;

const FadeIn = ({ children, delay = 0, style = {} }) => {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setVisible(true), delay);
    return () => clearTimeout(t);
  }, [delay]);
  return (
    <div
      style={{
        opacity: visible ? 1 : 0,
        transform: visible ? "translateY(0)" : "translateY(18px)",
        transition: "opacity 0.6s ease, transform 0.6s ease",
        ...style,
      }}
    >
      {children}
    </div>
  );
};

const Pill = ({ children, color = colors.accent, bg }) => (
  <span
    style={{
      display: "inline-block",
      padding: "3px 10px",
      borderRadius: 4,
      fontSize: 11,
      fontFamily: font,
      fontWeight: 600,
      color,
      background: bg || `${color}18`,
      letterSpacing: 0.5,
    }}
  >
    {children}
  </span>
);

const CommandRow = ({ cmd, desc, color = colors.accent }) => (
  <div
    style={{
      display: "flex",
      gap: 12,
      alignItems: "baseline",
      padding: "6px 0",
      borderBottom: `1px solid ${colors.border}`,
    }}
  >
    <code
      style={{
        fontFamily: font,
        fontSize: 12,
        color,
        whiteSpace: "nowrap",
        minWidth: 130,
      }}
    >
      {cmd}
    </code>
    <span style={{ fontSize: 12, color: colors.textDim, lineHeight: 1.4 }}>
      {desc}
    </span>
  </div>
);

const Section = ({ title, icon, children, delay = 0 }) => (
  <FadeIn delay={delay}>
    <div style={{ marginBottom: 28 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 12,
        }}
      >
        <span style={{ fontSize: 16 }}>{icon}</span>
        <h3
          style={{
            margin: 0,
            fontSize: 13,
            fontFamily: font,
            fontWeight: 700,
            color: colors.text,
            textTransform: "uppercase",
            letterSpacing: 1.5,
          }}
        >
          {title}
        </h3>
      </div>
      {children}
    </div>
  </FadeIn>
);

export default function AnalystOSInfographic() {
  const modes = [
    {
      name: "Discovery",
      desc: "Learn an unfamiliar database",
      color: colors.accent,
      bg: colors.accentDim,
      commands: [
        { cmd: "/db-orient", desc: "Schema overview — entities, relationships, key tables" },
        { cmd: "/db-explain", desc: "Plain-English explanation of any table or column" },
        { cmd: "/db-profile", desc: "Row counts, null rates, distinct values, distributions" },
      ],
    },
    {
      name: "Work",
      desc: "Write & validate queries",
      color: colors.orange,
      bg: colors.orangeDim,
      commands: [
        { cmd: "/db-query", desc: "Natural language → dialect-correct, cost-checked SQL" },
        { cmd: "/db-joins", desc: "Join paths, cardinality checks, fan-out risk flags" },
        { cmd: "/db-gotchas", desc: "Surface known traps: soft deletes, encodings, etc." },
      ],
    },
    {
      name: "Documentation",
      desc: "Capture what you've learned",
      color: colors.green,
      bg: colors.greenDim,
      commands: [
        { cmd: "/db-document", desc: "Draft or update a data dictionary entry" },
        { cmd: "/db-capture", desc: "Save a query + context as a canonical example" },
        { cmd: "/db-index", desc: "View and manage knowledge base inventory" },
      ],
    },
  ];

  const databases = [
    { name: "SQLite", note: "Bundled demo — no creds needed", icon: "◇" },
    { name: "Oracle", note: "EXPLAIN PLAN cost estimation", icon: "◆" },
    { name: "Snowflake", note: "Bytes-scanned cost gate", icon: "❄" },
    { name: "Athena", note: "Glue catalog, partition filters", icon: "△" },
    { name: "Salesforce", note: "SOQL, governor limits", icon: "☁" },
  ];

  return (
    <div
      style={{
        background: colors.bg,
        minHeight: "100vh",
        fontFamily: sansFont,
        color: colors.text,
        padding: "40px 24px",
        position: "relative",
        overflow: "hidden",
      }}
    >
      <link
        href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap"
        rel="stylesheet"
      />

      {/* Subtle grid background */}
      <div
        style={{
          position: "fixed",
          inset: 0,
          backgroundImage: `
            linear-gradient(${colors.border}44 1px, transparent 1px),
            linear-gradient(90deg, ${colors.border}44 1px, transparent 1px)
          `,
          backgroundSize: "60px 60px",
          pointerEvents: "none",
          zIndex: 0,
        }}
      />

      {/* Glow orb */}
      <div
        style={{
          position: "fixed",
          top: -120,
          right: -120,
          width: 400,
          height: 400,
          borderRadius: "50%",
          background: `radial-gradient(circle, ${colors.accent}15, transparent 70%)`,
          pointerEvents: "none",
          zIndex: 0,
        }}
      />

      <div style={{ maxWidth: 720, margin: "0 auto", position: "relative", zIndex: 1 }}>
        {/* Header */}
        <FadeIn delay={0}>
          <div style={{ marginBottom: 10 }}>
            <Pill color={colors.textMuted} bg={colors.surfaceLight}>
              CLAUDE CODE EXTENSION
            </Pill>
          </div>
          <h1
            style={{
              margin: 0,
              fontSize: 42,
              fontFamily: font,
              fontWeight: 700,
              color: colors.text,
              letterSpacing: -1,
              lineHeight: 1.1,
            }}
          >
            Analyst<span style={{ color: colors.accent }}>OS</span>
          </h1>
          <p
            style={{
              margin: "14px 0 0",
              fontSize: 15,
              color: colors.textDim,
              lineHeight: 1.6,
              maxWidth: 560,
            }}
          >
            A senior data engineer assistant that already knows the database,
            remembers every question asked, and drafts queries without being
            asked twice.
          </p>
        </FadeIn>

        {/* Divider */}
        <FadeIn delay={100}>
          <div
            style={{
              margin: "32px 0",
              height: 1,
              background: `linear-gradient(90deg, ${colors.accent}60, ${colors.border}, transparent)`,
            }}
          />
        </FadeIn>

        {/* Three Modes */}
        <Section title="Three Modes of Work" icon="⬡" delay={150}>
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            {modes.map((mode, i) => (
              <FadeIn key={mode.name} delay={200 + i * 100}>
                <div
                  style={{
                    background: colors.surface,
                    border: `1px solid ${colors.border}`,
                    borderRadius: 8,
                    padding: "16px 18px",
                    borderLeft: `3px solid ${mode.color}`,
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      marginBottom: 10,
                    }}
                  >
                    <Pill color={mode.color} bg={mode.bg}>
                      {mode.name.toUpperCase()}
                    </Pill>
                    <span style={{ fontSize: 12, color: colors.textMuted }}>
                      {mode.desc}
                    </span>
                  </div>
                  <div>
                    {mode.commands.map((c) => (
                      <CommandRow
                        key={c.cmd}
                        cmd={c.cmd}
                        desc={c.desc}
                        color={mode.color}
                      />
                    ))}
                  </div>
                </div>
              </FadeIn>
            ))}
          </div>

          {/* Utility commands */}
          <FadeIn delay={550}>
            <div
              style={{
                marginTop: 14,
                padding: "10px 14px",
                background: colors.surfaceLight,
                borderRadius: 6,
                border: `1px solid ${colors.border}`,
              }}
            >
              <span
                style={{
                  fontSize: 10,
                  fontFamily: font,
                  color: colors.textMuted,
                  textTransform: "uppercase",
                  letterSpacing: 1,
                }}
              >
                Utility
              </span>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "4px 20px", marginTop: 6 }}>
                {[
                  { c: "/db-status", d: "Active connection & KB inventory" },
                  { c: "/db-use", d: "Switch database connection" },
                ].map((u) => (
                  <CommandRow
                    key={u.c}
                    cmd={u.c}
                    desc={u.d}
                    color={colors.violet}
                  />
                ))}
              </div>
            </div>
          </FadeIn>
        </Section>

        {/* Safety */}
        <Section title="Safety Hooks" icon="⛨" delay={600}>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            {[
              {
                name: "db-safety",
                desc: "Blocks DDL/DML (DROP, DELETE, INSERT…) before execution. Explicit override required.",
                color: colors.red,
                bg: colors.redDim,
                icon: "🛡",
              },
              {
                name: "db-cost-gate",
                desc: "Flags expensive queries — full scans, missing partition filters — before they run.",
                color: colors.orange,
                bg: colors.orangeDim,
                icon: "⚡",
              },
            ].map((hook) => (
              <div
                key={hook.name}
                style={{
                  flex: "1 1 280px",
                  background: colors.surface,
                  border: `1px solid ${colors.border}`,
                  borderRadius: 8,
                  padding: "14px 16px",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                  <span style={{ fontSize: 16 }}>{hook.icon}</span>
                  <code
                    style={{
                      fontFamily: font,
                      fontSize: 13,
                      fontWeight: 600,
                      color: hook.color,
                    }}
                  >
                    {hook.name}
                  </code>
                  <Pill color={hook.color} bg={hook.bg}>
                    AUTO
                  </Pill>
                </div>
                <p style={{ margin: 0, fontSize: 12, color: colors.textDim, lineHeight: 1.5 }}>
                  {hook.desc}
                </p>
              </div>
            ))}
          </div>
        </Section>

        {/* Knowledge Base */}
        <Section title="Compounding Knowledge Base" icon="📂" delay={700}>
          <div
            style={{
              background: colors.surface,
              border: `1px solid ${colors.border}`,
              borderRadius: 8,
              padding: "16px 18px",
              position: "relative",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                position: "absolute",
                top: 0,
                right: 0,
                width: 120,
                height: 120,
                background: `radial-gradient(circle at top right, ${colors.green}10, transparent 70%)`,
                pointerEvents: "none",
              }}
            />
            <div
              style={{
                fontFamily: font,
                fontSize: 12,
                color: colors.textMuted,
                marginBottom: 12,
                lineHeight: 1.8,
              }}
            >
              <span style={{ color: colors.green }}>db-knowledge/</span>
              <br />
              {"  ├── "}
              <span style={{ color: colors.textDim }}>_schema-overview.md</span>
              <br />
              {"  ├── "}
              <span style={{ color: colors.textDim }}>_session-context.md</span>
              <br />
              {"  ├── "}
              <span style={{ color: colors.textDim }}>orders.md</span>
              <br />
              {"  ├── "}
              <span style={{ color: colors.textDim }}>customers.md</span>
              <br />
              {"  └── "}
              <span style={{ color: colors.textDim }}>…one file per table</span>
            </div>
            <p style={{ margin: 0, fontSize: 12, color: colors.textDim, lineHeight: 1.6 }}>
              Plain markdown, git-versioned, team-shared. Claude reads it at
              session start. The knowledge base is the durable product —{" "}
              <span style={{ color: colors.green }}>Claude is the tool that builds it</span>.
            </p>
          </div>
        </Section>

        {/* Databases */}
        <Section title="Supported Databases" icon="⬢" delay={800}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
            {databases.map((db) => (
              <div
                key={db.name}
                style={{
                  flex: "1 1 130px",
                  background: colors.surface,
                  border: `1px solid ${colors.border}`,
                  borderRadius: 8,
                  padding: "12px 14px",
                  textAlign: "center",
                }}
              >
                <div style={{ fontSize: 18, marginBottom: 4 }}>{db.icon}</div>
                <div
                  style={{
                    fontFamily: font,
                    fontSize: 13,
                    fontWeight: 700,
                    color: colors.text,
                    marginBottom: 4,
                  }}
                >
                  {db.name}
                </div>
                <div style={{ fontSize: 10, color: colors.textMuted, lineHeight: 1.4 }}>
                  {db.note}
                </div>
              </div>
            ))}
          </div>
        </Section>

        {/* Architecture flow */}
        <Section title="Architecture" icon="◈" delay={900}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              gap: 0,
              flexWrap: "wrap",
              padding: "10px 0",
            }}
          >
            {[
              { label: "Analyst", sub: "Slash commands", color: colors.accent },
              null,
              { label: "Claude Code", sub: "Skills + Hooks", color: colors.violet },
              null,
              { label: "Database", sub: "Read-only queries", color: colors.orange },
              null,
              { label: "Knowledge Base", sub: "db-knowledge/", color: colors.green },
            ].map((item, i) =>
              item === null ? (
                <div
                  key={`arrow-${i}`}
                  style={{
                    width: 30,
                    height: 2,
                    background: `linear-gradient(90deg, ${colors.border}, ${colors.textMuted})`,
                    position: "relative",
                    flexShrink: 0,
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      right: -3,
                      top: -4,
                      width: 0,
                      height: 0,
                      borderLeft: `6px solid ${colors.textMuted}`,
                      borderTop: "5px solid transparent",
                      borderBottom: "5px solid transparent",
                    }}
                  />
                </div>
              ) : (
                <div
                  key={item.label}
                  style={{
                    background: colors.surfaceLight,
                    border: `1px solid ${colors.border}`,
                    borderRadius: 6,
                    padding: "10px 14px",
                    textAlign: "center",
                    minWidth: 100,
                  }}
                >
                  <div
                    style={{
                      fontFamily: font,
                      fontSize: 11,
                      fontWeight: 700,
                      color: item.color,
                      marginBottom: 3,
                    }}
                  >
                    {item.label}
                  </div>
                  <div style={{ fontSize: 9, color: colors.textMuted }}>{item.sub}</div>
                </div>
              )
            )}
          </div>
        </Section>

        {/* Footer */}
        <FadeIn delay={1000}>
          <div
            style={{
              marginTop: 16,
              padding: "14px 0",
              borderTop: `1px solid ${colors.border}`,
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <span style={{ fontSize: 10, color: colors.textMuted, fontFamily: font }}>
              Read-only by default · Cost-gated · Git-versioned knowledge
            </span>
            <span style={{ fontSize: 10, color: colors.textMuted, fontFamily: font }}>
              AnalystOS v1
            </span>
          </div>
        </FadeIn>
      </div>
    </div>
  );
}
