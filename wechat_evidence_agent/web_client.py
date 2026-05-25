"""Local browser client for WeChat Evidence Agent."""

from __future__ import annotations

import json
import logging
import threading
import concurrent.futures
import webbrowser
import re
import mimetypes
import os
import subprocess
import sys
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse
from uuid import uuid4

from .config import Config
from .main import WeChatEvidenceApp
from .tools_center import generate_image_evidence_docx, get_tool_definitions

logger = logging.getLogger(__name__)


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>微信证据助手</title>
<style>
:root {
  --bg: #0a0b0f;
  --panel: #111318;
  --panel-2: #161820;
  --line: #2a2d3a;
  --line-2: #3d4155;
  --text: #e8e6e3;
  --muted: #9a97a0;
  --faint: #68656e;
  --gold: #c9a84c;
  --gold-2: #e8d48a;
  --cyan: #5bb8c4;
  --green: #5aad72;
  --red: #c45b5b;
}
* { box-sizing: border-box; }
.hidden { display: none !important; }
* {
  scrollbar-width: thin;
  scrollbar-color: rgba(201,168,76,.48) rgba(255,255,255,.035);
}
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: rgba(255,255,255,.035); border-radius: 999px; }
::-webkit-scrollbar-thumb {
  background: linear-gradient(180deg, rgba(201,168,76,.68), rgba(91,184,196,.50));
  border: 2px solid rgba(10,11,15,.92);
  border-radius: 999px;
}
::-webkit-scrollbar-thumb:hover {
  background: linear-gradient(180deg, rgba(232,212,138,.86), rgba(91,184,196,.72));
}
body {
  margin: 0;
  min-height: 100vh;
  background:
    radial-gradient(900px 520px at 20% -10%, rgba(201,168,76,.10), transparent 60%),
    linear-gradient(rgba(201,168,76,.025) 1px, transparent 1px),
    linear-gradient(90deg, rgba(201,168,76,.025) 1px, transparent 1px),
    var(--bg);
  background-size: auto, 72px 72px, 72px 72px, auto;
  color: var(--text);
  font-family: "Microsoft YaHei UI", "Noto Sans SC", system-ui, sans-serif;
}
button, input, textarea, select { font: inherit; }
.app { display: grid; grid-template-columns: 320px minmax(0, 1fr); height: 100vh; }
.side {
  border-right: 1px solid var(--line);
  background: rgba(17,19,24,.92);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
  overflow: hidden;
}
.brand { padding-bottom: 12px; border-bottom: 1px solid var(--line); }
.eyebrow {
  color: var(--gold);
  font-size: 11px;
  letter-spacing: 3px;
  text-transform: uppercase;
  margin-bottom: 10px;
}
.brand h1 {
  margin: 0;
  font-family: Georgia, "SimSun", serif;
  font-size: 24px;
  font-weight: 700;
  letter-spacing: 0;
}
.brand p { margin: 8px 0 0; color: var(--muted); font-size: 13px; line-height: 1.7; }
.new-chat-btn {
  width: 100%;
  border: 1px solid rgba(232,212,138,.58);
  background: rgba(201,168,76,.12);
  color: var(--text);
  border-radius: 8px;
  padding: 12px 13px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 700;
  margin-bottom: -12px;
}
.new-chat-btn:hover { border-color: var(--gold-2); background: rgba(201,168,76,.18); }
.new-chat-btn span:last-child {
  color: var(--gold-2);
  font-size: 18px;
  line-height: 1;
}
.side-section { display: grid; gap: 6px; min-height: 0; }
.side-section.history-section { flex: 1; margin-top: -8px; }
.workspace-switch {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  padding: 3px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255,255,255,.025);
}
.workspace-switch .nav-btn {
  border: 0;
  border-radius: 6px;
  padding: 9px 8px;
  text-align: center;
  font-size: 13px;
  background: transparent;
}
.workspace-switch .nav-btn.active {
  background: rgba(201,168,76,.14);
  color: var(--text);
}
.side-section-title {
  color: var(--faint);
  font-size: 12px;
  letter-spacing: .3px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.chat-history {
  min-height: 0;
  overflow: auto;
  display: grid;
  align-content: start;
  gap: 5px;
  padding-right: 2px;
}
.history-item {
  border: 1px solid transparent;
  background: transparent;
  color: var(--muted);
  border-radius: 8px;
  padding: 9px 10px;
  cursor: pointer;
  text-align: left;
  display: grid;
  gap: 4px;
}
.history-item:hover { background: rgba(255,255,255,.035); color: var(--text); }
.history-item.active {
  border-color: rgba(201,168,76,.42);
  background: rgba(201,168,76,.10);
  color: var(--text);
}
.history-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.history-meta { color: var(--faint); font-size: 11px; }
.history-empty {
  border: 1px dashed var(--line);
  border-radius: 8px;
  color: var(--faint);
  font-size: 12px;
  line-height: 1.6;
  padding: 12px;
}
.history-more {
  border: 0;
  background: transparent;
  color: var(--gold-2);
  border-radius: 7px;
  padding: 8px 10px;
  cursor: pointer;
  text-align: left;
  font-size: 12px;
}
.history-more:hover { background: rgba(201,168,76,.08); color: var(--text); }
.label { color: var(--faint); font-size: 12px; margin-bottom: 5px; }
.value {
  color: var(--text);
  font-size: 14px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ok { color: var(--green); }
.warn { color: var(--gold); }
.bad { color: var(--red); }
.side-footer {
  border-top: 1px solid var(--line);
  padding-top: 12px;
  display: grid;
  gap: 10px;
  margin-top: auto;
}
.connection-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-width: 0;
}
.connection-status {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  color: var(--muted);
  font-size: 12px;
  min-width: 0;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: var(--red);
  box-shadow: 0 0 0 3px rgba(196,91,91,.12);
  flex: 0 0 auto;
}
.status-dot.ok {
  background: var(--green);
  box-shadow: 0 0 0 3px rgba(90,173,114,.14);
}
.status-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.side-footer .btn {
  justify-content: flex-start;
  padding: 8px 10px;
  font-size: 13px;
}
.quick-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  padding-bottom: 10px;
}
.quick-actions .btn {
  min-width: 0;
  justify-content: center;
  padding: 8px 10px;
  font-size: 12px;
  border-radius: 999px;
}
.main-nav {
  display: grid;
  gap: 8px;
  padding-bottom: 4px;
}
.nav-btn {
  border: 1px solid var(--line);
  background: rgba(255,255,255,.025);
  color: var(--muted);
  border-radius: 8px;
  padding: 11px 12px;
  cursor: pointer;
  text-align: left;
}
.nav-btn:hover { border-color: var(--line-2); color: var(--text); }
.nav-btn.active {
  border-color: rgba(201,168,76,.62);
  background: rgba(201,168,76,.11);
  color: var(--text);
}
.btn {
  border: 1px solid var(--line-2);
  background: rgba(255,255,255,.035);
  color: var(--text);
  border-radius: 7px;
  padding: 10px 12px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 9px;
  justify-content: center;
}
.btn:hover { border-color: var(--gold); background: rgba(201,168,76,.08); }
.btn:disabled {
  opacity: .55;
  cursor: not-allowed;
}
.btn.primary {
  background: linear-gradient(135deg, rgba(201,168,76,.95), rgba(232,212,138,.92));
  border-color: rgba(232,212,138,.8);
  color: #17130a;
  font-weight: 700;
}
.btn.ghost { justify-content: flex-start; }
.main { min-width: 0; min-height: 0; }
.workspace { height: 100vh; min-width: 0; min-height: 0; }
.workspace.hidden { display: none; }
.case-workspace { display: grid; grid-template-rows: auto 1fr auto; }
.tools-workspace { display: grid; grid-template-rows: auto 1fr; }
.topbar {
  height: 72px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 28px;
  border-bottom: 1px solid var(--line);
  background: rgba(10,11,15,.72);
}
.topbar h2 { margin: 0; font-size: 18px; font-weight: 650; }
.topbar span { color: var(--muted); font-size: 13px; }
.topbar-actions { display: flex; align-items: center; gap: 10px; }
.chat {
  overflow: auto;
  padding: 26px 28px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}
.message { width: min(820px, 100%); display: grid; gap: 7px; }
.message.user { align-self: flex-end; }
.meta { font-size: 12px; color: var(--faint); }
.bubble {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 14px 16px;
  line-height: 1.75;
  white-space: pre-wrap;
  background: rgba(22,24,32,.92);
}
.previews {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 10px;
  margin-top: 10px;
}
.preview-card {
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
  background: rgba(255,255,255,.03);
  color: inherit;
  text-decoration: none;
  cursor: pointer;
  padding: 0;
  text-align: left;
}
.preview-card:hover { border-color: rgba(232,212,138,.65); }
.preview-card img {
  display: block;
  width: 100%;
  aspect-ratio: 1 / 1;
  object-fit: cover;
  background: #0d0f14;
}
.preview-caption {
  padding: 7px 8px;
  color: var(--muted);
  font-size: 11px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-placeholder {
  display: grid;
  place-items: center;
  min-height: 118px;
  padding: 12px;
  color: var(--gold-2);
  text-align: center;
  font-size: 12px;
  line-height: 1.5;
  background: rgba(201,168,76,.06);
}
.image-lightbox {
  position: fixed;
  inset: 0;
  z-index: 50;
  display: none;
  align-items: center;
  justify-content: center;
  padding: 28px;
  background: rgba(0,0,0,.78);
}
.image-lightbox.open { display: flex; }
.image-lightbox-panel {
  width: min(1180px, 100%);
  height: min(820px, 92vh);
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  background: #090a0d;
  border: 1px solid var(--line-2);
  border-radius: 8px;
  box-shadow: 0 26px 90px rgba(0,0,0,.62);
  overflow: hidden;
}
.image-lightbox-head,
.image-lightbox-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--line);
  color: var(--muted);
  font-size: 13px;
}
.image-lightbox-foot { border-top: 1px solid var(--line); border-bottom: 0; }
.image-title {
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.image-stage {
  position: relative;
  display: grid;
  place-items: center;
  min-width: 0;
  min-height: 0;
  padding: 18px 64px;
}
.image-stage img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}
.image-nav,
.image-close {
  border: 1px solid var(--line-2);
  background: rgba(255,255,255,.06);
  color: var(--text);
  border-radius: 7px;
  cursor: pointer;
}
.image-close { width: 34px; height: 34px; }
.image-nav {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 42px;
  height: 54px;
  font-size: 26px;
}
.image-nav:hover,
.image-close:hover { border-color: var(--gold); background: rgba(201,168,76,.12); }
.image-prev { left: 14px; }
.image-next { right: 14px; }
.image-counter { color: var(--gold-2); white-space: nowrap; }
.user .bubble {
  background: rgba(201,168,76,.12);
  border-color: rgba(201,168,76,.32);
}
.system .bubble {
  border-color: rgba(91,184,196,.25);
  background: rgba(91,184,196,.06);
}
.process-panel {
  display: grid;
  gap: 10px;
}
.process-head {
  display: flex;
  align-items: center;
  gap: 9px;
}
.process-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(232,212,138,.22);
  border-top-color: var(--gold-2);
  border-radius: 999px;
  animation: spin .8s linear infinite;
}
.process-steps {
  display: grid;
  gap: 6px;
  color: var(--muted);
  font-size: 12px;
}
.process-step {
  display: flex;
  align-items: center;
  gap: 7px;
}
.process-step::before {
  content: "";
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: var(--faint);
  opacity: .55;
}
.process-step.active { color: var(--gold-2); }
.process-step.active::before {
  background: var(--gold-2);
  opacity: 1;
  box-shadow: 0 0 0 4px rgba(201,168,76,.10);
}
.process-elapsed { color: var(--faint); font-size: 12px; }
@keyframes spin { to { transform: rotate(360deg); } }
.welcome-panel {
  width: min(880px, 100%);
  margin: auto auto 0;
  display: grid;
  gap: 18px;
}
.welcome-title {
  display: grid;
  gap: 8px;
}
.welcome-title h3 {
  margin: 0;
  font-size: 26px;
  font-weight: 700;
}
.welcome-title p {
  margin: 0;
  color: var(--muted);
  line-height: 1.75;
}
.prompt-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.prompt-card {
  border: 1px solid var(--line);
  background: rgba(22,24,32,.78);
  color: var(--text);
  border-radius: 8px;
  padding: 13px 14px;
  cursor: pointer;
  text-align: left;
  line-height: 1.55;
}
.prompt-card:hover { border-color: rgba(232,212,138,.62); background: rgba(201,168,76,.08); }
.prompt-card small {
  display: block;
  margin-top: 4px;
  color: var(--faint);
  font-size: 12px;
}
.composer {
  border-top: 1px solid var(--line);
  padding: 14px 28px 22px;
  background: rgba(10,11,15,.88);
}
.composer-inner {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 104px;
  gap: 12px;
  align-items: end;
}
textarea {
  width: 100%;
  min-width: 0;
  min-height: 54px;
  max-height: 170px;
  resize: vertical;
  border: 1px solid var(--line-2);
  border-radius: 8px;
  background: var(--panel);
  color: var(--text);
  padding: 13px 14px;
  outline: none;
}
textarea:focus { border-color: var(--gold); box-shadow: 0 0 0 3px rgba(201,168,76,.10); }
.hint { margin-top: 8px; color: var(--faint); font-size: 12px; }
.tools-content {
  overflow: hidden;
  padding: 26px 28px;
  min-height: 0;
}
.tools-home { display: grid; gap: 18px; max-width: 980px; }
.section-title { display: grid; gap: 6px; }
.section-title h3 { margin: 0; font-size: 18px; }
.section-title p { margin: 0; color: var(--muted); line-height: 1.7; }
.tool-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 14px;
}
.tool-card,
.tool-panel,
.tool-result {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(22,24,32,.86);
}
.tool-card {
  padding: 16px;
  display: grid;
  gap: 12px;
}
.tool-card h4 { margin: 0; font-size: 16px; }
.tool-card p { margin: 0; color: var(--muted); line-height: 1.65; font-size: 13px; }
.tool-tag { color: var(--gold-2); font-size: 12px; }
.tool-detail {
  max-width: 1320px;
  height: 100%;
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 16px;
}
.tool-detail.hidden { display: none; }
.tool-panel { padding: 16px; display: grid; gap: 15px; }
.tool-head {
  display: grid;
  gap: 8px;
}
.tool-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.tool-title-row h3 { margin: 0; }
.tool-title-row .btn { padding: 8px 10px; }
.image-docx-layout {
  display: grid;
  grid-template-columns: minmax(320px, 430px) minmax(0, 1fr);
  gap: 16px;
  align-items: start;
  min-height: 0;
}
.preview-panel {
  height: 100%;
  min-height: 0;
  padding: 16px;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  gap: 14px;
}
.preview-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.preview-pages {
  display: grid;
  align-content: start;
  gap: 18px;
  min-height: 0;
  overflow: auto;
  padding: 2px 10px 18px 2px;
  overscroll-behavior: contain;
}
.word-page {
  width: min(100%, 540px);
  aspect-ratio: 210 / 297;
  margin: 0 auto;
  padding: 20px;
  border: 1px solid rgba(255,255,255,.12);
  border-radius: 4px;
  background: #f8f6ef;
  color: #1b1b1b;
  box-shadow: 0 18px 40px rgba(0,0,0,.32);
  display: grid;
  grid-template-rows: 1fr;
  gap: 10px;
}
.word-page.has-title { grid-template-rows: auto 1fr; }
.word-page-title {
  text-align: center;
  font-weight: 700;
  font-size: 13px;
  line-height: 1.2;
}
.word-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  grid-template-rows: 1fr 1fr;
  gap: 8px;
  min-height: 0;
}
.preview-slot {
  position: relative;
  border: 1px solid #d8d2c4;
  background: #fff;
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  gap: 5px;
  padding: 6px;
  cursor: grab;
  transition: border-color .12s ease, box-shadow .12s ease, transform .12s ease, opacity .12s ease;
  min-width: 0;
  min-height: 0;
}
.preview-slot:hover {
  border-color: #b79b45;
  box-shadow: 0 0 0 2px rgba(183,155,69,.28);
  transform: translateY(-1px);
}
.preview-slot:active { cursor: grabbing; }
.preview-slot.dragging {
  cursor: grabbing;
  opacity: .42;
  outline: 2px solid var(--gold);
  box-shadow: 0 0 0 3px rgba(201,168,76,.24);
}
.preview-slot.drop-target {
  cursor: copy;
  outline: 3px solid var(--cyan);
  box-shadow: 0 0 0 4px rgba(91,184,196,.20);
}
.image-docx-dragging,
.image-docx-dragging * { cursor: grabbing !important; }
.preview-slot img {
  width: 100%;
  height: 100%;
  min-height: 0;
  object-fit: contain;
  background: #f4f4f4;
}
.word-preview-caption {
  color: #333;
  font-size: 8px;
  line-height: 1.25;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: center;
}
.preview-empty {
  display: grid;
  place-items: center;
  min-height: 420px;
  border: 1px dashed var(--line-2);
  border-radius: 8px;
  color: var(--muted);
  text-align: center;
  line-height: 1.7;
  padding: 24px;
}
.upload-box {
  border: 1px dashed var(--line-2);
  border-radius: 8px;
  padding: 18px;
  display: grid;
  gap: 10px;
  background: rgba(255,255,255,.02);
}
.form-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 12px;
}
.option-row {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  align-items: center;
}
.check-row {
  min-height: 28px;
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--muted);
}
.check-row input { width: 16px; height: 16px; }
.file-list {
  max-height: 210px;
  overflow: auto;
  display: grid;
  gap: 6px;
}
.file-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 12px;
  padding: 8px 10px;
  border: 1px solid rgba(255,255,255,.06);
  border-radius: 7px;
  color: var(--muted);
  font-size: 12px;
}
.file-item.active { border-color: rgba(201,168,76,.5); color: var(--text); }
.file-item span:first-child {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mini-btn {
  border: 1px solid rgba(255,255,255,.12);
  background: rgba(255,255,255,.04);
  color: var(--muted);
  border-radius: 6px;
  padding: 4px 8px;
  cursor: pointer;
  font-size: 12px;
}
.mini-btn:hover {
  border-color: var(--red);
  color: #ffd6d6;
  background: rgba(196,91,91,.14);
}
.preview-remove {
  position: absolute;
  top: 5px;
  right: 5px;
  width: 22px;
  height: 22px;
  padding: 0;
  display: grid;
  place-items: center;
  border-radius: 999px;
  border: 1px solid rgba(0,0,0,.20);
  background: rgba(255,255,255,.88);
  color: #6d1d1d;
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  opacity: 0;
}
.preview-slot:hover .preview-remove,
.preview-remove:focus {
  opacity: 1;
}
.preview-remove:hover {
  background: #c45b5b;
  color: #fff;
}
.tool-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.tool-result { padding: 14px; color: var(--muted); line-height: 1.75; display: none; }
.tool-result.open { display: block; }
.result-actions { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0; }
.tool-result a { color: var(--gold-2); text-decoration: none; }
.tool-result a:hover { text-decoration: underline; }
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,.62);
  display: none;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.modal {
  width: min(620px, 100%);
  background: var(--panel);
  border: 1px solid var(--line-2);
  border-radius: 8px;
  box-shadow: 0 24px 80px rgba(0,0,0,.55);
}
.modal header, .modal footer { padding: 18px 20px; border-bottom: 1px solid var(--line); }
.modal footer { border-top: 1px solid var(--line); border-bottom: 0; display: flex; justify-content: flex-end; gap: 10px; }
.modal h3 { margin: 0; font-size: 17px; }
.modal-body { padding: 18px 20px; display: grid; gap: 14px; }
.field { display: grid; gap: 7px; }
.field label { color: var(--muted); font-size: 13px; }
.field input, .field select {
  border: 1px solid var(--line-2);
  border-radius: 7px;
  background: #0d0f14;
  color: var(--text);
  padding: 10px 11px;
  outline: none;
}
.field input:focus, .field select:focus { border-color: var(--gold); }
@media (max-width: 860px) {
  .app { grid-template-columns: 1fr; }
  .side { display: none; }
  .topbar { padding: 0 18px; }
  .chat, .composer { padding-left: 18px; padding-right: 18px; }
  .prompt-grid { grid-template-columns: 1fr; }
  .tools-content { padding: 18px; overflow: auto; }
  .tool-detail { height: auto; }
  .image-docx-layout { grid-template-columns: 1fr; }
  .preview-panel { min-height: 520px; }
  .form-grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div class="app">
  <aside class="side">
    <section class="brand">
      <div class="eyebrow">WeChat Evidence Agent</div>
      <h1>微信证据助手</h1>
      <p>面向律师的微信电子证据提取、分析与文书生成工作台。</p>
    </section>
    <button class="new-chat-btn" onclick="newChatSession()"><span>New chat</span><span>+</span></button>
    <section class="side-section history-section">
      <div class="side-section-title">Chat history</div>
      <div class="chat-history" id="chatHistory"></div>
    </section>
    <section class="main-nav workspace-switch" aria-label="工作区切换">
      <button class="nav-btn active" id="navCase" onclick="showWorkspace('case')">案件工作台</button>
      <button class="nav-btn" id="navTools" onclick="showWorkspace('tools')">常用工具</button>
    </section>
    <section class="side-footer">
      <button class="btn ghost" id="configButton" onclick="openConfig()" title="正在检查 AI 连接状态">
        <span class="status-dot" id="aiStatusDot"></span>
        <span id="aiStatusText">AI 未连接</span>
      </button>
    </section>
  </aside>
  <main class="main">
    <section class="workspace case-workspace" id="caseWorkspace">
      <header class="topbar">
        <div>
          <h2 id="caseTitle">新对话</h2>
          <span id="caseSubtitle">像聊天一样提取微信材料、整理案件事实、进入案情分析</span>
        </div>
        <div class="topbar-actions">
          <button class="btn" onclick="refreshStatus()">刷新状态</button>
        </div>
      </header>
      <section class="chat" id="chat">
        <article class="message system">
          <div class="meta">系统</div>
          <div class="bubble">您好，我是微信证据助手。请描述案件事实、联系人备注名或需要提取的聊天范围。我会优先自动定位微信材料，找不到时再提示您选择目录。</div>
        </article>
      </section>
      <section class="composer">
        <section class="quick-actions" aria-label="常用操作">
          <button class="btn ghost" onclick="sendQuick('帮我列出联系人')">列出联系人</button>
          <button class="btn ghost" onclick="sendQuick('帮我分析当前案件的证据链')">分析证据链</button>
          <button class="btn ghost" onclick="resetChat()">重置对话</button>
        </section>
        <div class="composer-inner">
          <textarea id="input" placeholder="例如：帮我查一下本地跟蔚青的聊天，并提取涉及借款的消息"></textarea>
          <button class="btn primary" id="send" onclick="sendMessage()">发送</button>
        </div>
        <div class="hint">Enter 发送，Shift + Enter 换行。普通用户界面会隐藏内部 traceback，仅展示可操作的错误说明。</div>
      </section>
    </section>
    <section class="workspace tools-workspace hidden" id="toolsWorkspace">
      <header class="topbar">
        <div>
          <h2>常用工具</h2>
          <span>批量文件处理、证据文档排版与材料整理</span>
        </div>
        <button class="btn" onclick="showWorkspace('case')">返回案件工作台</button>
      </header>
      <section class="tools-content">
        <div class="tools-home" id="toolsHome">
          <div class="section-title">
            <h3>工具中心</h3>
            <p>第一期先提供确定性文件工具，后续会继续加入 PDF、哈希校验、证据目录等常用律师工作流。</p>
          </div>
          <div class="tool-grid" id="toolGrid">
            <article class="tool-card">
              <div class="tool-tag">证据文档 · Word</div>
              <h4>图片证据排版</h4>
              <p>导入多张聊天截图、转账截图或照片，按 A4 一页四张生成可编辑 Word。</p>
              <button class="btn primary" onclick="openImageDocxTool()">打开工具</button>
            </article>
          </div>
        </div>
        <div class="tool-detail hidden" id="imageDocxTool">
          <div class="tool-head">
            <div class="section-title">
              <div class="tool-title-row">
                <button class="btn" onclick="showToolsHome()">返回</button>
                <h3>图片证据排版</h3>
              </div>
              <p>支持 jpg、jpeg、png、bmp、webp，单次最多处理 50 张。右侧预览即 Word 中的一页四张排列，可拖拽调整顺序。</p>
            </div>
          </div>
          <div class="image-docx-layout">
            <section class="tool-panel">
              <div class="upload-box">
                <input id="imageDocxFiles" type="file" accept=".jpg,.jpeg,.png,.bmp,.webp,image/*" multiple hidden onchange="imageFilesChanged()">
                <div class="tool-actions">
                  <button class="btn primary" onclick="document.getElementById('imageDocxFiles').click()">选择图片</button>
                  <button class="btn" onclick="clearImageDocxTool()">清空</button>
                </div>
                <div class="hint" id="imageDocxCount">尚未选择图片。</div>
                <div class="file-list" id="imageDocxFileList"></div>
              </div>
              <div class="form-grid">
                <div class="field">
                  <label>文档标题</label>
                  <input id="imageDocxTitle" placeholder="可选，留空则不显示标题" oninput="renderImageDocxPreview()">
                </div>
                <div class="option-row">
                  <label class="check-row"><input id="imageDocxShowIndex" type="checkbox" checked onchange="renderImageDocxPreview()"> 显示序号</label>
                  <label class="check-row"><input id="imageDocxShowFilename" type="checkbox" checked onchange="renderImageDocxPreview()"> 显示文件名</label>
                </div>
              </div>
              <div class="tool-actions">
                <button class="btn primary" id="imageDocxRun" onclick="runImageDocxTool()">生成 Word</button>
              </div>
              <div class="tool-result" id="imageDocxResult"></div>
            </section>
            <section class="tool-panel preview-panel">
              <div class="preview-toolbar">
                <div>
                  <div class="label">排版预览</div>
                  <div class="value" id="imageDocxPreviewCount">尚未选择图片</div>
                </div>
                <div class="hint">拖动图片卡片调整导出顺序</div>
              </div>
              <div id="imageDocxPreview" class="preview-pages">
                <div class="preview-empty">选择图片后，这里会显示 Word 页面中的一页四张排版效果。</div>
              </div>
            </section>
          </div>
        </div>
      </section>
    </section>
  </main>
</div>

<div class="modal-backdrop" id="configModal">
  <section class="modal">
    <header><h3>大模型配置</h3></header>
    <div class="modal-body">
      <div class="field">
        <label>服务商</label>
        <select id="provider" onchange="providerChanged()">
          <option value="deepseek">DeepSeek</option>
          <option value="openai">OpenAI</option>
          <option value="custom">自定义 OpenAI 兼容接口</option>
        </select>
      </div>
      <div class="field"><label>Base URL</label><input id="baseUrl" placeholder="https://api.deepseek.com"></div>
      <div class="field"><label>模型名称</label><input id="modelInput" placeholder="deepseek-chat"></div>
      <div class="field"><label>API Key</label><input id="apiKey" type="password" placeholder="留空则保持不变"></div>
    </div>
    <footer>
      <button class="btn" onclick="closeModals()">取消</button>
      <button class="btn primary" onclick="saveConfig()">保存</button>
    </footer>
  </section>
</div>

<div class="modal-backdrop" id="dirModal">
  <section class="modal">
    <header><h3>微信数据目录</h3></header>
    <div class="modal-body">
      <div class="field">
        <label>目录路径</label>
        <input id="dirInput" placeholder="C:\Users\用户名\Documents\xwechat_files">
      </div>
      <div class="hint">可以填 xwechat_files 根目录，也可以填具体 wxid 账号目录。</div>
    </div>
    <footer>
      <button class="btn" onclick="closeModals()">取消</button>
      <button class="btn primary" onclick="saveDir()">保存</button>
    </footer>
  </section>
</div>

<script>
const chat = document.getElementById("chat");
const input = document.getElementById("input");
let statusCache = {};
let lightboxImages = [];
let lightboxIndex = 0;
let selectedImageDocxFiles = [];
let imageDocxPreviewUrls = new Map();
let draggedImageDocxIndex = null;
const CHAT_STORE_KEY = "wechatEvidenceChats.v1";
const HISTORY_COLLAPSE_LIMIT = 5;
let chatSessions = [];
let currentChatId = "";
let showAllChatHistory = false;

function makeChatId() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return "chat-" + Date.now() + "-" + Math.random().toString(16).slice(2);
}

function createChatSession() {
  const now = Date.now();
  return {id: makeChatId(), title: "新对话", createdAt: now, updatedAt: now, messages: []};
}

function loadChatSessions() {
  try {
    const stored = JSON.parse(localStorage.getItem(CHAT_STORE_KEY) || "[]");
    if (Array.isArray(stored)) chatSessions = stored.filter(item => item && item.id);
  } catch (err) {
    chatSessions = [];
  }
  if (!chatSessions.length) chatSessions = [createChatSession()];
  currentChatId = chatSessions[0].id;
  saveChatSessions();
  renderChatHistory();
  renderCurrentChat();
}

function saveChatSessions() {
  localStorage.setItem(CHAT_STORE_KEY, JSON.stringify(chatSessions.slice(0, 40)));
}

function getCurrentSession() {
  let session = chatSessions.find(item => item.id === currentChatId);
  if (!session) {
    session = createChatSession();
    chatSessions.unshift(session);
    currentChatId = session.id;
  }
  return session;
}

function updateCurrentTitle(session, text) {
  if (!session || session.title !== "新对话") return;
  const title = String(text || "").replace(/\s+/g, " ").trim();
  if (title) session.title = title.slice(0, 22);
}

function appendMessageToCurrent(role, text, images) {
  const session = getCurrentSession();
  session.messages.push({role, text, images: images || [], time: Date.now()});
  if (role === "user") updateCurrentTitle(session, text);
  session.updatedAt = Date.now();
  chatSessions = [session].concat(chatSessions.filter(item => item.id !== session.id));
  saveChatSessions();
  renderChatHistory();
  updateCaseHeader(session);
}

function updateCaseHeader(session) {
  const title = document.getElementById("caseTitle");
  const subtitle = document.getElementById("caseSubtitle");
  if (!title || !subtitle) return;
  title.textContent = session.title || "新对话";
  const count = (session.messages || []).length;
  subtitle.textContent = count ? `${count} 条上下文消息，继续围绕当前案件整理材料` : "像聊天一样提取微信材料、整理案件事实、进入案情分析";
}

function renderChatHistory() {
  const list = document.getElementById("chatHistory");
  if (!list) return;
  list.innerHTML = "";
  if (!chatSessions.length) {
    list.innerHTML = `<div class="history-empty">暂无历史对话</div>`;
    return;
  }
  const visibleSessions = showAllChatHistory ? chatSessions : chatSessions.slice(0, HISTORY_COLLAPSE_LIMIT);
  visibleSessions.forEach(session => {
    const button = document.createElement("button");
    button.className = "history-item" + (session.id === currentChatId ? " active" : "");
    button.type = "button";
    button.onclick = () => selectChatSession(session.id);
    const title = document.createElement("div");
    title.className = "history-title";
    title.textContent = session.title || "新对话";
    const meta = document.createElement("div");
    meta.className = "history-meta";
    meta.textContent = formatChatTime(session.updatedAt);
    button.append(title, meta);
    list.append(button);
  });
  if (chatSessions.length > HISTORY_COLLAPSE_LIMIT) {
    const more = document.createElement("button");
    more.className = "history-more";
    more.type = "button";
    more.textContent = showAllChatHistory ? "Show less" : `Show more (${chatSessions.length - HISTORY_COLLAPSE_LIMIT})`;
    more.onclick = () => {
      showAllChatHistory = !showAllChatHistory;
      renderChatHistory();
    };
    list.append(more);
  }
}

function formatChatTime(value) {
  const date = value ? new Date(value) : new Date();
  const today = new Date();
  if (date.toDateString() === today.toDateString()) {
    return date.toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit"});
  }
  return date.toLocaleDateString("zh-CN", {month: "2-digit", day: "2-digit"});
}

function welcomeHtml() {
  return `
    <section class="welcome-panel">
      <div class="welcome-title">
        <h3>今天处理哪个案件？</h3>
        <p>你可以直接说要查谁的聊天、要证明什么事实。我会优先定位微信材料，再把文字和图片证据整理进案情分析。</p>
      </div>
      <div class="prompt-grid">
        <button class="prompt-card" onclick="sendQuick('帮我查一下本地跟蔚青的聊天，并整理关键事实')">查找联系人聊天<small>按备注、昵称或微信号提取材料</small></button>
        <button class="prompt-card" onclick="sendQuick('帮我分析当前案件的证据链，区分事实、证据和待补材料')">分析证据链<small>把聊天内容整理成律师可用结构</small></button>
        <button class="prompt-card" onclick="sendQuick('帮我列出联系人')">列出联系人<small>自动定位微信目录并检查联系人索引</small></button>
        <button class="prompt-card" onclick="sendQuick('帮我从最近的聊天里找出可用于案件分析的图片证据')">整理图片证据<small>提取聊天图片，进入证据预览和分析</small></button>
      </div>
    </section>`;
}

function renderCurrentChat() {
  const session = getCurrentSession();
  updateCaseHeader(session);
  chat.innerHTML = "";
  if (!session.messages.length) {
    chat.innerHTML = welcomeHtml();
  } else {
    session.messages.forEach(message => addMessage(message.role, message.text, {persist: false, images: message.images || []}));
  }
  chat.scrollTop = chat.scrollHeight;
}

async function newChatSession() {
  const session = createChatSession();
  chatSessions.unshift(session);
  currentChatId = session.id;
  saveChatSessions();
  renderChatHistory();
  renderCurrentChat();
  showWorkspace("case");
  try {
    await api("/api/reset", {});
  } catch (err) {
    addMessage("system", err.message, {persist: false});
  }
}

function selectChatSession(id) {
  currentChatId = id;
  renderChatHistory();
  renderCurrentChat();
  showWorkspace("case");
}

function showWorkspace(name) {
  document.getElementById("caseWorkspace").classList.toggle("hidden", name !== "case");
  document.getElementById("toolsWorkspace").classList.toggle("hidden", name !== "tools");
  document.getElementById("navCase").classList.toggle("active", name === "case");
  document.getElementById("navTools").classList.toggle("active", name === "tools");
}

function openImageDocxTool() {
  showWorkspace("tools");
  document.getElementById("toolsHome").classList.add("hidden");
  document.getElementById("imageDocxTool").classList.remove("hidden");
}

function showToolsHome() {
  document.getElementById("imageDocxTool").classList.add("hidden");
  document.getElementById("toolsHome").classList.remove("hidden");
}

function imageFilesChanged() {
  const inputEl = document.getElementById("imageDocxFiles");
  const additions = Array.from(inputEl.files || []);
  selectedImageDocxFiles = selectedImageDocxFiles.concat(additions);
  inputEl.value = "";
  renderImageDocxFiles();
  renderImageDocxPreview();
}

function renderImageDocxFiles() {
  const count = document.getElementById("imageDocxCount");
  const list = document.getElementById("imageDocxFileList");
  list.innerHTML = "";
  if (!selectedImageDocxFiles.length) {
    count.textContent = "尚未选择图片。";
    renderImageDocxPreview();
    return;
  }
  count.textContent = `已选择 ${selectedImageDocxFiles.length} 张图片，超过 50 张时只处理前 50 张。`;
  selectedImageDocxFiles.slice(0, 50).forEach((file, index) => {
    const item = document.createElement("div");
    item.className = "file-item";
    const name = document.createElement("span");
    name.textContent = `${index + 1}. ${file.name}`;
    const size = document.createElement("span");
    size.textContent = formatBytes(file.size);
    const remove = document.createElement("button");
    remove.className = "mini-btn";
    remove.type = "button";
    remove.textContent = "移除";
    remove.addEventListener("click", () => removeImageDocxFile(index));
    item.append(name, size, remove);
    list.append(item);
  });
}

function removeImageDocxFile(index) {
  if (index < 0 || index >= selectedImageDocxFiles.length) return;
  const [removed] = selectedImageDocxFiles.splice(index, 1);
  const url = imageDocxPreviewUrls.get(removed);
  if (url) URL.revokeObjectURL(url);
  imageDocxPreviewUrls.delete(removed);
  renderImageDocxFiles();
  renderImageDocxPreview();
}

function clearImageDocxTool() {
  imageDocxPreviewUrls.forEach(url => URL.revokeObjectURL(url));
  imageDocxPreviewUrls = new Map();
  selectedImageDocxFiles = [];
  document.getElementById("imageDocxFiles").value = "";
  document.getElementById("imageDocxResult").classList.remove("open");
  document.getElementById("imageDocxResult").innerHTML = "";
  renderImageDocxFiles();
  renderImageDocxPreview();
}

function renderImageDocxPreview() {
  const wrap = document.getElementById("imageDocxPreview");
  const count = document.getElementById("imageDocxPreviewCount");
  if (!wrap || !count) return;
  wrap.innerHTML = "";
  const files = selectedImageDocxFiles.slice(0, 50);
  if (!files.length) {
    count.textContent = "尚未选择图片";
    wrap.innerHTML = `<div class="preview-empty">选择图片后，这里会显示 Word 页面中的一页四张排版效果。</div>`;
    return;
  }
  count.textContent = `${files.length} 张图片，预计 ${Math.ceil(files.length / 4)} 页`;
  const title = document.getElementById("imageDocxTitle").value.trim();
  for (let start = 0; start < files.length; start += 4) {
    const page = document.createElement("div");
    page.className = "word-page";
    const grid = document.createElement("div");
    grid.className = "word-grid";
    if (title && start === 0) {
      page.classList.add("has-title");
      const pageTitle = document.createElement("div");
      pageTitle.className = "word-page-title";
      pageTitle.textContent = title;
      page.append(pageTitle);
    }
    files.slice(start, start + 4).forEach((file, offset) => {
      grid.append(createPreviewSlot(file, start + offset));
    });
    for (let i = files.slice(start, start + 4).length; i < 4; i += 1) {
      const empty = document.createElement("div");
      empty.className = "preview-slot";
      empty.style.cursor = "default";
      grid.append(empty);
    }
    page.append(grid);
    wrap.append(page);
  }
}

function createPreviewSlot(file, index) {
  const slot = document.createElement("div");
  slot.className = "preview-slot";
  slot.draggable = true;
  slot.dataset.index = String(index);
  slot.addEventListener("dragstart", event => {
    draggedImageDocxIndex = index;
    document.body.classList.add("image-docx-dragging");
    slot.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", String(index));
  });
  slot.addEventListener("dragend", () => {
    draggedImageDocxIndex = null;
    document.body.classList.remove("image-docx-dragging");
    document.querySelectorAll(".preview-slot").forEach(item => item.classList.remove("dragging", "drop-target"));
  });
  slot.addEventListener("dragover", event => {
    event.preventDefault();
    slot.classList.add("drop-target");
  });
  slot.addEventListener("dragleave", () => slot.classList.remove("drop-target"));
  slot.addEventListener("drop", event => {
    event.preventDefault();
    slot.classList.remove("drop-target");
    const from = draggedImageDocxIndex ?? Number(event.dataTransfer.getData("text/plain"));
    moveImageDocxFile(from, index);
  });

  const img = document.createElement("img");
  img.src = getImageDocxPreviewUrl(file);
  img.alt = file.name;
  const caption = document.createElement("div");
  caption.className = "word-preview-caption";
  caption.textContent = imageDocxCaption(file, index);
  const remove = document.createElement("button");
  remove.className = "preview-remove";
  remove.type = "button";
  remove.title = "移除这张图片";
  remove.textContent = "×";
  remove.addEventListener("click", event => {
    event.preventDefault();
    event.stopPropagation();
    removeImageDocxFile(index);
  });
  remove.addEventListener("dragstart", event => event.preventDefault());
  slot.append(img, caption, remove);
  return slot;
}

function moveImageDocxFile(from, to) {
  if (!Number.isInteger(from) || !Number.isInteger(to) || from === to) return;
  if (from < 0 || to < 0 || from >= selectedImageDocxFiles.length || to >= selectedImageDocxFiles.length) return;
  const [file] = selectedImageDocxFiles.splice(from, 1);
  selectedImageDocxFiles.splice(to, 0, file);
  renderImageDocxFiles();
  renderImageDocxPreview();
}

function getImageDocxPreviewUrl(file) {
  if (!imageDocxPreviewUrls.has(file)) {
    imageDocxPreviewUrls.set(file, URL.createObjectURL(file));
  }
  return imageDocxPreviewUrls.get(file);
}

function imageDocxCaption(file, index) {
  const showIndex = document.getElementById("imageDocxShowIndex").checked;
  const showFilename = document.getElementById("imageDocxShowFilename").checked;
  const parts = [];
  if (showIndex) parts.push(`图${index + 1}`);
  if (showFilename) parts.push(shortName(file.name, 36));
  if (parts.length === 2) return `${parts[0]}：${parts[1]}`;
  return parts[0] || "";
}

function shortName(name, maxLength) {
  if (name.length <= maxLength) return name;
  const dot = name.lastIndexOf(".");
  const suffix = dot > 0 ? name.slice(dot) : "";
  const stem = dot > 0 ? name.slice(0, dot) : name;
  return stem.slice(0, Math.max(10, maxLength - suffix.length - 3)) + "..." + suffix;
}

async function runImageDocxTool() {
  if (!selectedImageDocxFiles.length) {
    showToolResult("请先选择需要排版的图片。", false);
    return;
  }
  const button = document.getElementById("imageDocxRun");
  button.disabled = true;
  button.textContent = "正在生成...";
  showToolResult("正在生成 Word，请稍候。", true);
  try {
    const form = new FormData();
    selectedImageDocxFiles.forEach(file => form.append("images", file, file.name));
    form.append("title", document.getElementById("imageDocxTitle").value.trim());
    form.append("show_index", document.getElementById("imageDocxShowIndex").checked ? "true" : "false");
    form.append("show_filename", document.getElementById("imageDocxShowFilename").checked ? "true" : "false");

    const res = await fetch("/api/tools/image-evidence-docx", {method: "POST", body: form});
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || "生成失败");
    const fileUrl = `/api/file?path=${encodeURIComponent(data.file_path)}`;
    const openFilePayload = escapeHtml(JSON.stringify({path: data.file_path}));
    const openFolderPayload = escapeHtml(JSON.stringify({path: data.file_path}));
    const skipped = (data.skipped || []).map(item => `<div>${escapeHtml(item.file || "")}：${escapeHtml(item.reason || "")}</div>`).join("");
    showToolResult(`
      <div class="ok">Word 已生成：${data.image_count} 张图片，${data.page_count} 页。</div>
      <div class="result-actions">
        <button class="btn primary" onclick="openLocalFile(${openFilePayload})">一键打开 Word</button>
        <button class="btn" onclick="openLocalFolder(${openFolderPayload})">查看文件位置</button>
        <a class="btn" href="${fileUrl}" target="_blank" rel="noreferrer">浏览器下载</a>
      </div>
      <div>${escapeHtml(data.file_path)}</div>
      ${skipped ? `<div class="warn">跳过文件：</div>${skipped}` : ""}
    `, true);
  } catch (err) {
    showToolResult(escapeHtml(err.message), false);
  } finally {
    button.disabled = false;
    button.textContent = "生成 Word";
  }
}

function showToolResult(html, ok) {
  const result = document.getElementById("imageDocxResult");
  result.classList.add("open");
  result.classList.toggle("bad", !ok);
  result.innerHTML = html;
}

async function openLocalFile(payload) {
  try {
    await api("/api/open-file", payload);
  } catch (err) {
    showToolResult(escapeHtml(err.message), false);
  }
}

async function openLocalFolder(payload) {
  try {
    await api("/api/open-folder", payload);
  } catch (err) {
    showToolResult(escapeHtml(err.message), false);
  }
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / Math.pow(1024, index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, char => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[char]));
}

function addMessage(role, text, options = {}) {
  const persist = options.persist !== false;
  const images = options.images || [];
  if (persist && chat.querySelector(".welcome-panel")) chat.innerHTML = "";
  const item = document.createElement("article");
  item.className = "message " + role;
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = role === "user" ? "律师" : role === "system" ? "系统" : "助手";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  item.append(meta, bubble);
  chat.append(item);
  renderImages(item, images);
  if (persist) appendMessageToCurrent(role, text, images);
  chat.scrollTop = chat.scrollHeight;
  return item;
}

function renderImages(item, images) {
  if (!images || !images.length) return;
  const previewImages = images.filter(image => image && image.url);
  const wrap = document.createElement("div");
  wrap.className = "previews";
  images.forEach(image => {
    const card = document.createElement(image.url ? "button" : "div");
    card.className = "preview-card";
    if (image.url) {
      card.type = "button";
      card.addEventListener("click", () => {
        const index = previewImages.findIndex(candidate => candidate.url === image.url);
        openLightbox(previewImages, Math.max(index, 0));
      });
      const img = document.createElement("img");
      img.src = image.url;
      img.alt = image.name || "image evidence";
      card.append(img);
    } else {
      const box = document.createElement("div");
      box.className = "preview-placeholder";
      box.textContent = image.error || image.status || "图片待解密";
      card.append(box);
    }
    const caption = document.createElement("div");
    caption.className = "preview-caption";
    caption.textContent = image.name || image.path || "图片证据";
    card.append(caption);
    wrap.append(card);
  });
  item.append(wrap);
  chat.scrollTop = chat.scrollHeight;
}

function startProcessIndicator(item, userText) {
  const bubble = item.querySelector(".bubble");
  const steps = inferProcessSteps(userText);
  const startedAt = Date.now();
  bubble.innerHTML = `
    <div class="process-panel">
      <div class="process-head">
        <span class="process-spinner"></span>
        <strong>正在处理请求</strong>
      </div>
      <div class="process-steps"></div>
      <div class="process-elapsed">已用时 0 秒</div>
    </div>`;
  const stepsWrap = bubble.querySelector(".process-steps");
  const elapsed = bubble.querySelector(".process-elapsed");

  function render() {
    const seconds = Math.floor((Date.now() - startedAt) / 1000);
    const activeIndex = Math.min(Math.floor(seconds / 4), steps.length - 1);
    stepsWrap.innerHTML = "";
    steps.forEach((step, index) => {
      const row = document.createElement("div");
      row.className = "process-step" + (index === activeIndex ? " active" : "");
      row.textContent = index < activeIndex ? `${step} ✓` : step;
      stepsWrap.append(row);
    });
    elapsed.textContent = `已用时 ${seconds} 秒`;
    chat.scrollTop = chat.scrollHeight;
  }

  render();
  const timer = setInterval(render, 1000);
  return () => clearInterval(timer);
}

function inferProcessSteps(text) {
  const content = String(text || "");
  if (/聊天|联系人|记录|微信|图片|证据/.test(content)) {
    return ["理解律师意图", "定位微信数据与联系人", "解密并提取聊天材料", "整理文字与图片证据", "生成可读分析结果"];
  }
  return ["理解律师意图", "选择合适工具", "执行处理", "整理输出结果"];
}

function ensureLightbox() {
  let box = document.getElementById("imageLightbox");
  if (box) return box;
  box = document.createElement("div");
  box.id = "imageLightbox";
  box.className = "image-lightbox";
  box.innerHTML = `
    <section class="image-lightbox-panel" role="dialog" aria-modal="true" aria-label="图片预览">
      <div class="image-lightbox-head">
        <div class="image-title" id="imageLightboxTitle">图片证据</div>
        <button class="image-close" type="button" aria-label="关闭" onclick="closeLightbox()">X</button>
      </div>
      <div class="image-stage">
        <button class="image-nav image-prev" type="button" aria-label="上一张" onclick="moveLightbox(-1)">&lt;</button>
        <img id="imageLightboxImg" alt="图片证据">
        <button class="image-nav image-next" type="button" aria-label="下一张" onclick="moveLightbox(1)">&gt;</button>
      </div>
      <div class="image-lightbox-foot">
        <div class="image-counter" id="imageLightboxCounter"></div>
        <div id="imageLightboxPath"></div>
      </div>
    </section>`;
  box.addEventListener("click", event => {
    if (event.target === box) closeLightbox();
  });
  document.body.append(box);
  return box;
}

function openLightbox(images, index) {
  lightboxImages = images || [];
  lightboxIndex = index || 0;
  ensureLightbox().classList.add("open");
  showLightbox(lightboxIndex);
}

function showLightbox(index) {
  if (!lightboxImages.length) return;
  lightboxIndex = (index + lightboxImages.length) % lightboxImages.length;
  const image = lightboxImages[lightboxIndex];
  document.getElementById("imageLightboxImg").src = image.url;
  document.getElementById("imageLightboxTitle").textContent = image.name || "图片证据";
  document.getElementById("imageLightboxCounter").textContent = `${lightboxIndex + 1} / ${lightboxImages.length}`;
  document.getElementById("imageLightboxPath").textContent = image.path || "";
}

function moveLightbox(delta) {
  showLightbox(lightboxIndex + delta);
}

function closeLightbox() {
  const box = document.getElementById("imageLightbox");
  if (box) box.classList.remove("open");
}

async function api(path, payload) {
  const options = payload === undefined ? {} : {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload)
  };
  const res = await fetch(path, options);
  const data = await res.json();
  if (!res.ok || data.ok === false) throw new Error(data.error || "请求失败");
  return data;
}

async function refreshStatus() {
  try {
    statusCache = await api("/api/status");
    const connected = !!statusCache.api_key_configured;
    const dot = document.getElementById("aiStatusDot");
    const text = document.getElementById("aiStatusText");
    const configButton = document.getElementById("configButton");
    if (dot) dot.classList.toggle("ok", connected);
    if (text) text.textContent = connected ? "AI 已连接" : "AI 未连接";
    if (configButton) {
      const model = statusCache.model || "-";
      configButton.title = connected ? `AI 已连接：${model}` : "AI 未连接：点击配置模型和 API Key";
    }
  } catch (err) {
    const dot = document.getElementById("aiStatusDot");
    const text = document.getElementById("aiStatusText");
    const configButton = document.getElementById("configButton");
    if (dot) dot.classList.remove("ok");
    if (text) text.textContent = "AI 状态异常";
    if (configButton) configButton.title = err.message;
    addMessage("system", err.message, {persist: false});
  }
}

async function sendMessage() {
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addMessage("user", text);
  const pending = addMessage("assistant", "正在处理...", {persist: false});
  const stopProcessIndicator = startProcessIndicator(pending, text);
  document.getElementById("send").disabled = true;
  try {
    const data = await api("/api/chat", {message: text});
    stopProcessIndicator();
    pending.querySelector(".bubble").textContent = data.response;
    renderImages(pending, data.images);
    appendMessageToCurrent("assistant", data.response, data.images || []);
  } catch (err) {
    stopProcessIndicator();
    pending.querySelector(".bubble").textContent = err.message;
    appendMessageToCurrent("assistant", err.message, []);
  } finally {
    document.getElementById("send").disabled = false;
    chat.scrollTop = chat.scrollHeight;
  }
}

function sendQuick(text) {
  input.value = text;
  sendMessage();
}

async function resetChat() {
  await api("/api/reset", {});
  const session = getCurrentSession();
  session.messages = [];
  session.title = "新对话";
  session.updatedAt = Date.now();
  saveChatSessions();
  renderChatHistory();
  renderCurrentChat();
  addMessage("system", "对话已重置，可以重新描述案件或选择联系人。");
}

function openConfig() {
  document.getElementById("provider").value = statusCache.base_url === "" || statusCache.base_url === null ? "openai" : (statusCache.base_url === "https://api.deepseek.com" ? "deepseek" : "custom");
  document.getElementById("baseUrl").value = statusCache.base_url || "";
  document.getElementById("modelInput").value = statusCache.model || "deepseek-chat";
  document.getElementById("apiKey").value = "";
  document.getElementById("configModal").style.display = "flex";
}

function openDir() {
  document.getElementById("dirInput").value = statusCache.wechat_dir || "";
  document.getElementById("dirModal").style.display = "flex";
}

function closeModals() {
  document.querySelectorAll(".modal-backdrop").forEach(m => m.style.display = "none");
}

function providerChanged() {
  const p = document.getElementById("provider").value;
  if (p === "deepseek") {
    document.getElementById("baseUrl").value = "https://api.deepseek.com";
    document.getElementById("modelInput").value = "deepseek-chat";
  } else if (p === "openai") {
    document.getElementById("baseUrl").value = "";
    document.getElementById("modelInput").value = "gpt-4o";
  }
}

async function saveConfig() {
  try {
    await api("/api/config", {
      base_url: document.getElementById("baseUrl").value.trim(),
      model: document.getElementById("modelInput").value.trim(),
      api_key: document.getElementById("apiKey").value.trim()
    });
    closeModals();
    await refreshStatus();
    addMessage("system", "大模型配置已保存。", {persist: false});
  } catch (err) {
    addMessage("system", err.message, {persist: false});
  }
}

async function saveDir() {
  try {
    const data = await api("/api/wechat-dir", {path: document.getElementById("dirInput").value.trim()});
    closeModals();
    await refreshStatus();
    addMessage("system", "微信目录已设置为：" + data.wechat_dir, {persist: false});
  } catch (err) {
    addMessage("system", err.message, {persist: false});
  }
}

input.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});
document.addEventListener("keydown", e => {
  const box = document.getElementById("imageLightbox");
  if (!box || !box.classList.contains("open")) return;
  if (e.key === "Escape") closeLightbox();
  if (e.key === "ArrowLeft") moveLightbox(-1);
  if (e.key === "ArrowRight") moveLightbox(1);
});
loadChatSessions();
refreshStatus();
</script>
</body>
</html>
"""


class _ClientHandler(BaseHTTPRequestHandler):
    app: WeChatEvidenceApp
    config_path: Path
    lock: threading.Lock

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("web client: " + fmt, *args)

    def _json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def _send_file(self, path: Path) -> None:
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            self._json({"ok": False, "error": "文件不存在。"}, 404)
            return
        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        data = resolved.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/status":
            cfg = self.app.config
            self._json({
                "ok": True,
                "model": cfg.openai_model,
                "base_url": cfg.openai_base_url,
                "api_key_configured": bool(cfg.openai_api_key),
                "wechat_dir": cfg.wechat_dir or "",
            })
            return
        if path == "/api/tools":
            self._json({
                "ok": True,
                "tools": [tool.to_public_dict() for tool in get_tool_definitions()],
            })
            return
        if path == "/api/file":
            query = parse_qs(parsed.query)
            raw_path = (query.get("path") or [""])[0]
            if not raw_path:
                self._json({"ok": False, "error": "缺少文件路径。"}, 400)
                return
            self._send_file(Path(raw_path))
            return
        self._json({"ok": False, "error": "未找到该页面。"}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/tools/image-evidence-docx":
                with self.lock:
                    result = self._run_image_evidence_docx_tool()
                self._json(result, 200 if result.get("ok") else 400)
                return

            payload = self._read_json()
            with self.lock:
                if path == "/api/chat":
                    message = str(payload.get("message", "")).strip()
                    if not message:
                        raise ValueError("请输入要发送的内容。")
                    chat_handler = (
                        self.app.graph_agent.chat
                        if hasattr(self.app, "graph_agent")
                        else self.app.agent.chat
                    )
                    response = _call_with_timeout(
                        chat_handler,
                        message,
                        timeout=90,
                    )
                    self._json({
                        "ok": True,
                        "response": response,
                        "images": self._collect_preview_images(),
                    })
                    return

                if path == "/api/reset":
                    self.app.agent.reset()
                    if hasattr(self.app, "graph_agent"):
                        self.app.graph_agent.reset()
                    self._json({"ok": True})
                    return

                if path == "/api/config":
                    self._save_config(payload)
                    self._json({"ok": True})
                    return

                if path == "/api/wechat-dir":
                    result = self._save_wechat_dir(payload)
                    self._json({"ok": True, **result})
                    return

                if path == "/api/open-file":
                    self._open_local_path(payload, reveal=False)
                    self._json({"ok": True})
                    return

                if path == "/api/open-folder":
                    self._open_local_path(payload, reveal=True)
                    self._json({"ok": True})
                    return

            self._json({"ok": False, "error": "未知操作。"}, 404)
        except Exception as exc:
            logger.exception("Web client request failed: %s", path)
            self._json({"ok": False, "error": _friendly_error(exc)}, 400)

    def _read_multipart(self) -> tuple[dict[str, str], list[dict[str, Any]]]:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("请使用 multipart/form-data 上传图片。")
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            raise ValueError("上传内容为空。")
        body = self.rfile.read(length)
        raw = (
            f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
            + body
        )
        message = BytesParser(policy=email_policy).parsebytes(raw)
        fields: dict[str, str] = {}
        files: list[dict[str, Any]] = []
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            data = part.get_payload(decode=True) or b""
            filename = part.get_filename()
            if filename:
                files.append({
                    "field": name,
                    "filename": _safe_upload_name(filename),
                    "content": data,
                })
            else:
                charset = part.get_content_charset() or "utf-8"
                fields[name] = data.decode(charset, errors="ignore")
        return fields, files

    def _run_image_evidence_docx_tool(self) -> dict[str, Any]:
        fields, files = self._read_multipart()
        image_files = [file for file in files if file.get("field") == "images"]
        if not image_files:
            raise ValueError("请至少选择一张图片。")

        output_root = Path(self.app.config.output_path) / "tools" / "image_evidence_docx"
        upload_dir = output_root / "uploads" / uuid4().hex
        upload_dir.mkdir(parents=True, exist_ok=True)

        saved_paths: list[Path] = []
        for index, file in enumerate(image_files, start=1):
            filename = str(file["filename"]) or f"image_{index}.jpg"
            target = upload_dir / f"{index:03d}_{filename}"
            target.write_bytes(file["content"])
            saved_paths.append(target)

        title = fields.get("title", "").strip()
        show_filename = _as_bool(fields.get("show_filename", "true"))
        show_index = _as_bool(fields.get("show_index", "true"))
        return generate_image_evidence_docx(
            saved_paths,
            title=title,
            show_filename=show_filename,
            show_index=show_index,
            output_root=output_root,
        )

    def _open_local_path(self, payload: dict[str, Any], *, reveal: bool) -> None:
        raw_path = str(payload.get("path", "")).strip()
        if not raw_path:
            raise ValueError("缺少文件路径。")
        target = Path(raw_path).expanduser()
        if not target.is_absolute():
            target = Path.cwd() / target
        target = target.resolve()
        if not target.exists():
            raise ValueError("文件不存在，可能已被移动或删除。")
        if reveal:
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen([opener, str(target.parent)])
            return
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, str(target)])

    def _save_config(self, payload: dict[str, Any]) -> None:
        cfg = self.app.config
        base_url = str(payload.get("base_url", "")).strip()
        model = str(payload.get("model", "")).strip() or "deepseek-chat"
        api_key = "".join(str(payload.get("api_key", "")).split())

        if api_key and api_key.count("sk-") > 1:
            raise ValueError("API Key 看起来被重复粘贴了，请只粘贴一次。")

        cfg.openai_base_url = base_url or None
        cfg.openai_model = model
        if api_key:
            cfg.openai_api_key = api_key
        cfg.save_to_file(self.config_path, redact_secrets=False)
        self.app._reload_llm_clients()

    def _save_wechat_dir(self, payload: dict[str, Any]) -> dict[str, str]:
        raw_path = str(payload.get("path", "")).strip().strip('"')
        if not raw_path:
            raise ValueError("请输入微信数据目录。")
        resolved = self.app.db_extractor._resolve_wechat_dir(Path(raw_path))
        self.app.db_extractor._wechat_dir = resolved
        self.app.config.wechat_dir = str(resolved)
        self.app.config.save_to_file(self.config_path, redact_secrets=False)
        return {"wechat_dir": str(resolved)}

    def _collect_preview_images(self) -> list[dict[str, str]]:
        state = getattr(getattr(self.app, "graph_agent", None), "state", {}) or {}
        images: list[dict[str, str]] = []
        seen: set[str] = set()
        max_preview_images = 80
        for item in state.get("image_evidence") or []:
            candidates = [item]
            thumb = item.get("thumbnail_evidence") if isinstance(item, dict) else None
            if isinstance(thumb, dict):
                candidates.append(thumb)
            for candidate in candidates:
                decoded = str(candidate.get("decoded_path") or "")
                if not decoded or decoded in seen:
                    continue
                path = Path(decoded)
                if not path.is_file():
                    continue
                if not _looks_like_image(path):
                    continue
                seen.add(decoded)
                images.append({
                    "path": decoded,
                    "name": candidate.get("source_name") or path.name,
                    "url": f"/api/file?path={quote(decoded)}",
                    "status": str(candidate.get("status") or ""),
                })
                if len(images) >= max_preview_images:
                    return images
            if isinstance(item, dict) and item.get("status") in {"decode_failed", "missing"} and len(images) < max_preview_images:
                images.append({
                    "path": str(item.get("source_path") or ""),
                    "name": str(item.get("source_name") or "图片证据"),
                    "url": "",
                    "status": str(item.get("status") or ""),
                    "error": _short_image_error(str(item.get("error") or "")),
                })
        return images

    def _handle_local_shortcut(self, message: str) -> str | None:
        normalized = re.sub(r"\s+", "", message)
        if "联系人" in normalized and any(word in normalized for word in ("列出", "搜索", "查找", "找")):
            keyword = ""
            match = re.search(r"(?:联系人|备注|昵称)(?:里|中)?(?:叫|含|包含|是)?([\u4e00-\u9fa5A-Za-z0-9_^.-]{1,24})", normalized)
            if match:
                keyword = match.group(1)
            return self.app.agent.tool_executor.execute("list_contacts", {"keyword": keyword})

        if "聊天" in normalized and any(word in normalized for word in ("提取", "查看", "查", "找", "导出")):
            patterns = [
                r"本地(?:和|与|跟)?([\u4e00-\u9fa5A-Za-z0-9_^.-]{2,24})的聊天",
                r"(?:和|与|跟)([\u4e00-\u9fa5A-Za-z0-9_^.-]{2,24})(?:的)?聊天",
                r"(?:提取|查看|查一下|查|找|导出)(?:一下)?(?:我)?(?:本地)?(?:和|与|跟)?([\u4e00-\u9fa5A-Za-z0-9_^.-]{2,24})(?:的)?聊天",
            ]
            for pattern in patterns:
                match = re.search(pattern, normalized)
                if match:
                    contact = re.sub(r"^(我)?(本地)?(和|与|跟)", "", match.group(1))
                    contact = re.sub(r"(的|聊天|记录|最近|部分)+$", "", contact)
                    return self.app.agent.tool_executor.execute("extract_chat", {"contact": contact})
        return None


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "当前操作耗时过长，已停止等待。请先确认微信数据库密钥配置，或缩小操作范围后重试。"
    text = str(exc) or exc.__class__.__name__
    if "hmac check failed" in text or "file is not a database" in text:
        return (
            "已找到微信数据库，但暂时无法解密。请确认微信已登录，"
            "或提供有效的 WECHAT_DB_KEY 后重试。"
        )
    if "Authentication" in text or "401" in text:
        return "大模型 API Key 校验失败，请在左侧重新配置。"
    return text


def _looks_like_image(path: Path) -> bool:
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        return True
    except OSError:
        return False
    except Exception:
        return False


def _safe_upload_name(filename: str) -> str:
    name = Path(filename).name.strip() or "image"
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name[:120] or "image"


def _as_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _short_image_error(error: str) -> str:
    if "新版图片 AES Key" in error or "AES Key" in error:
        return "已找到图片记录，但还没拿到新版微信图片密钥。请在微信里打开这张或最近一张图片后重新分析。"
    if "cannot identify image file" in error:
        return "已找到图片文件，但当前解码结果不是有效图片。"
    return error[:120] if error else "图片暂不可预览"


def _call_with_timeout(func: Any, *args: Any, timeout: int = 45) -> Any:
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(func, *args)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        raise TimeoutError() from exc
    finally:
        if future.done():
            executor.shutdown(wait=False, cancel_futures=True)


def run_web_client(
    config: Config | None = None,
    config_path: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Start the local browser client."""
    app = WeChatEvidenceApp(config=config or Config.get_default_config())
    config_path = config_path or Path.cwd() / "config.yaml"

    handler = type(
        "WeChatEvidenceClientHandler",
        (_ClientHandler,),
        {"app": app, "config_path": config_path, "lock": threading.Lock()},
    )

    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{server.server_port}/"
    print(f"微信证据助手客户端已启动：{url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n客户端已关闭。")
    finally:
        server.server_close()
