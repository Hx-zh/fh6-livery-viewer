#pragma once

#include <QString>

// FH6LiveryViewer local addition: viewer UI language.
//
// The host app passes --lang with its own interface language (BCP 47). Standalone runs fall back
// to FH6_LANG, then English. Part names come from the game's own string tables
// (tools/update_part_names.py -> assets/cars/part_names.<lang>.json); the viewer's own chrome
// strings go through tr(), with one table per interface language (English is the source text).
namespace viewer_i18n {

// One of "zh-cn", "zh-tw", "en", "ja", "ko".
QString languageCode();
void setLanguageCode(const QString &raw);

// True for the CJK interfaces (zh-cn / zh-tw / ja / ko). Their glyphs render visually smaller
// than Latin at the same point size, so the viewer bumps the base font and widens the side panel.
bool isCjk();

// Viewer chrome string: English source text -> current language. English returns the source;
// a missing entry also returns the source.
QString tr(const QString &source);

// Part option name for a car model code ("TOY_2000GT_69"), part type and option id.
// Returns an empty string when the catalog has no entry; callers keep their own fallback.
QString partName(const QString &modelCode, int partType, int optionId);

// Category name (Body Kit / Front Bumper / ...) from the same catalog. Empty when unknown.
QString partTypeName(int partType);

} // namespace viewer_i18n
