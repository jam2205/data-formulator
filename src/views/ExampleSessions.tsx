// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import React from 'react';
import {
    Typography,
    Box,
    Card,
} from '@mui/material';
import { StreamIcon } from '../icons';
import { textVar } from '../app/layout';

// Example session data for pre-built sessions
export interface ExampleSession {
    id: string;
    title: string;
    description: string;
    previewImage: string;
    workspace: string;       // path to workspace zip (e.g. /demos/demo_movies.zip)
    live: boolean;
}

// Loaded from /demos/demos.yaml at runtime; empty until fetched.
let _cachedSessions: ExampleSession[] | null = null;

/** Fetch the demo manifest (cached after first call). */
export async function fetchExampleSessions(): Promise<ExampleSession[]> {
    if (_cachedSessions) return _cachedSessions;
    try {
        const res = await fetch('/demos/demos.yaml');
        if (!res.ok) return [];
        const text = await res.text();
        // Minimal YAML list-of-objects parser (no dependency needed for this simple format)
        const entries = parseSimpleYamlList(text);
        _cachedSessions = entries.map((e: any) => ({
            id: e.id || '',
            title: e.title || '',
            description: e.description || '',
            previewImage: e.preview || '',
            workspace: e.workspace || '',
            live: e.live === true || e.live === 'true',
        }));
        return _cachedSessions;
    } catch {
        return [];
    }
}

/** Parse a simple YAML list of flat objects (no nested structures). */
function parseSimpleYamlList(text: string): Record<string, any>[] {
    const items: Record<string, any>[] = [];
    let current: Record<string, any> | null = null;
    for (const line of text.split('\n')) {
        const trimmed = line.trimEnd();
        if (trimmed.startsWith('- ')) {
            if (current) items.push(current);
            current = {};
            const kv = trimmed.slice(2);
            const colonIdx = kv.indexOf(': ');
            if (colonIdx > 0) {
                current[kv.slice(0, colonIdx).trim()] = parseYamlValue(kv.slice(colonIdx + 2).trim());
            }
        } else if (trimmed.startsWith('  ') && current) {
            const kv = trimmed.trim();
            const colonIdx = kv.indexOf(': ');
            if (colonIdx > 0) {
                current[kv.slice(0, colonIdx).trim()] = parseYamlValue(kv.slice(colonIdx + 2).trim());
            }
        }
    }
    if (current) items.push(current);
    return items;
}

function parseYamlValue(v: string): any {
    if (v === 'true') return true;
    if (v === 'false') return false;
    if (v === 'null' || v === '~') return null;
    if (/^-?\d+$/.test(v)) return parseInt(v, 10);
    if (/^-?\d+\.\d+$/.test(v)) return parseFloat(v);
    return v;
}

// Legacy hardcoded list — kept as fallback if manifest fails to load.
// Empty on purpose: Hugh.Quant shows no upstream demo studies. Cards come only from
// /demos/demos.yaml, which is empty too (see the note there).
export const exampleSessions: ExampleSession[] = [];

// Session card component for displaying example sessions
export const ExampleSessionCard: React.FC<{
    session: ExampleSession;
    onClick: () => void;
    disabled?: boolean;
}> = ({ session, onClick, disabled }) => {
    return (
        <Card
            variant="outlined"
            sx={{
                textAlign: 'left',
                cursor: disabled ? 'default' : 'pointer',
                display: 'flex',
                alignItems: 'stretch',
                gap: 0,
                p: 0,
                overflow: 'hidden',
                borderColor: 'rgba(255, 255, 255, 0.18)',
                boxShadow: '0 1px 3px rgba(32, 33, 36, 0.06)',
                '&:hover': disabled ? {} : {
                    transform: 'translateY(-2px)',
                    borderColor: 'primary.light',
                    boxShadow: '0 4px 12px rgba(32, 33, 36, 0.12)',
                },
            }}
            onClick={disabled ? undefined : onClick}
        >
            <Box
                sx={{
                    width: 72,
                    flexShrink: 0,
                    overflow: 'hidden',
                }}
            >
                <Box
                    component="img"
                    src={session.previewImage}
                    alt={session.title}
                    sx={{
                        width: '100%',
                        height: '100%',
                        objectFit: 'cover',
                        display: 'block',
                    }}
                />
            </Box>

            <Box sx={{ flex: 1, minWidth: 0, p: 1.5 }}>
                <Typography variant="body2" fontWeight={500} noWrap sx={{ color: 'text.primary' }}>
                    {session.live && <StreamIcon sx={{ fontSize: textVar.xxs, color: 'success.main', mr: 0.5 }} />}
                    {session.title}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{
                    fontSize: textVar.xs,
                    display: '-webkit-box',
                    WebkitLineClamp: 2,
                    WebkitBoxOrient: 'vertical',
                    overflow: 'hidden',
                    lineHeight: 1.3,
                    mt: 0.25,
                }}>
                    {session.description}
                </Typography>
            </Box>
        </Card>
    );
};
