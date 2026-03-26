'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Send, Utensils, Loader2, MapPin, ExternalLink, Star, Clock, ChevronDown, ChevronUp, Download, X } from 'lucide-react';

// ── Types ────────────────────────────────────────────────────────────────────

interface OpeningHours {
  weekday_text: string[];
  periods: any[];
}

interface Restaurant {
  name: string;
  description: string;
  address: string;
  maps_url: string;
  photo_url: string;
  rating?: number;
  opening_hours?: OpeningHours;
  recommended_because?: string;
  rank_reason?: string;
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
  restaurants?: Restaurant[];
}

// ── Constants ─────────────────────────────────────────────────────────────────

const MAX_VISIBLE = 3;

// ── TagFilterDropdown ─────────────────────────────────────────────────────────

interface TagCatalog {
  cuisines: string[];
  budgets: string[];
  locations: string[];
}

function TagCategoryDropdown({
  categoryKey,
  label,
  emoji,
  activeValue,
  options,
  activeClass,
  onSelect,
  onClear,
  openKey,
  setOpenKey,
}: {
  categoryKey: string;
  label: string;
  emoji: string;
  activeValue?: string;
  options: string[];
  activeClass: string;
  onSelect: (category: string, value: string) => void;
  onClear: (category: string) => void;
  openKey: string | null;
  setOpenKey: (k: string | null) => void;
}) {
  const open = openKey === categoryKey;
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (open && ref.current && !ref.current.contains(e.target as Node)) {
        setOpenKey(null);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open, setOpenKey]);

  return (
    <div ref={ref} className="relative flex-1">
      {/* Trigger */}
      <button
        onClick={() => setOpenKey(open ? null : categoryKey)}
        className={`flex items-center justify-between gap-1.5 w-full px-3 py-2 rounded-xl border text-sm transition-colors ${
          activeValue
            ? `${activeClass} border-transparent font-medium`
            : 'bg-gray-50 border-gray-200 text-gray-500 hover:bg-gray-100'
        }`}
      >
        <span className="truncate">
          {activeValue ? `${label}: ${activeValue}` : `${emoji} ${label}`}
        </span>
        {open ? <ChevronUp className="w-3.5 h-3.5 shrink-0" /> : <ChevronDown className="w-3.5 h-3.5 shrink-0" />}
      </button>

      {/* Dropdown panel */}
      {open && (
        <div className="absolute bottom-full mb-1 left-0 min-w-[200px] w-max max-w-xs bg-white border border-gray-200 rounded-xl shadow-lg z-50">
          <div className="px-3 pt-3 pb-1 flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-400 uppercase tracking-wide">{emoji} {label}</span>
            {activeValue && (
              <button
                onClick={() => { onClear(categoryKey); setOpenKey(null); }}
                className="text-xs text-gray-400 hover:text-red-500 flex items-center gap-0.5 transition-colors"
              >
                <X className="w-3 h-3" /> Clear
              </button>
            )}
          </div>
          <div className="px-3 pb-3 flex flex-wrap gap-1.5 max-h-48 overflow-y-auto">
            {options.map(opt => (
              <button
                key={opt}
                onClick={() => { onSelect(categoryKey, opt); setOpenKey(null); }}
                className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                  opt === activeValue
                    ? `${activeClass} ring-2 ring-offset-1 ring-current`
                    : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                }`}
              >
                {opt}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function TagFilterDropdown({
  activeTags,
  catalog,
  onSelect,
  onClear,
}: {
  activeTags: Record<string, string>;
  catalog: TagCatalog;
  onSelect: (category: string, value: string) => void;
  onClear: (category: string) => void;
}) {
  const [openKey, setOpenKey] = useState<string | null>(null);

  return (
    <div className="flex gap-2 mb-2">
      <TagCategoryDropdown
        categoryKey="cuisine" label="Cuisine" emoji="🍽️"
        activeValue={activeTags.cuisine} options={catalog.cuisines}
        activeClass="bg-orange-100 text-orange-700"
        onSelect={onSelect} onClear={onClear}
        openKey={openKey} setOpenKey={setOpenKey}
      />
      <TagCategoryDropdown
        categoryKey="location" label="Location" emoji="📍"
        activeValue={activeTags.location} options={catalog.locations}
        activeClass="bg-blue-100 text-blue-700"
        onSelect={onSelect} onClear={onClear}
        openKey={openKey} setOpenKey={setOpenKey}
      />
      <TagCategoryDropdown
        categoryKey="budget" label="Budget" emoji="💰"
        activeValue={activeTags.budget} options={catalog.budgets}
        activeClass="bg-green-100 text-green-700"
        onSelect={onSelect} onClear={onClear}
        openKey={openKey} setOpenKey={setOpenKey}
      />
    </div>
  );
}

// ── OpeningHoursDropdown ──────────────────────────────────────────────────────

function OpeningHoursDropdown({ hours }: { hours: OpeningHours }) {
  const [open, setOpen] = useState(false);

  const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  const todayIndex = new Date().getDay();
  const todayName = days[todayIndex];

  const todayText = hours.weekday_text.find(t => t.startsWith(todayName));
  const todayHours = todayText ? todayText.split(': ').slice(1).join(': ') : 'Hours unavailable';

  return (
    <div className="text-sm">
      <button
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 text-gray-500 hover:text-orange-500 transition-colors"
      >
        <Clock className="w-3.5 h-3.5 shrink-0 text-orange-400" />
        <span>
          <span className="font-medium text-gray-700">Today: </span>
          {todayHours}
        </span>
        <span className="text-orange-400 ml-1">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <ul className="mt-2 ml-5 space-y-0.5 text-gray-500">
          {hours.weekday_text.map((line, i) => {
            const [day, ...rest] = line.split(': ');
            const isToday = day === todayName;
            return (
              <li key={i} className={isToday ? 'font-semibold text-orange-500' : ''}>
                <span>{day}: </span>
                <span>{rest.join(': ')}</span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// ── WhyChosenDropdown ─────────────────────────────────────────────────────────

function WhyChosenDropdown({
  recommended_because,
  rank_reason,
  rank,
}: {
  recommended_because?: string;
  rank_reason?: string;
  rank: number;
}) {
  const [open, setOpen] = useState(false);

  if (!recommended_because && !rank_reason) return null;

  return (
    <div className="border border-orange-100 rounded-xl overflow-hidden text-sm">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between gap-2 px-3 py-2 bg-orange-50 hover:bg-orange-100 transition-colors text-left"
      >
        <span className="font-semibold text-orange-600 flex items-center gap-1.5">
          <span className="text-base"></span>
          Why #{rank}?
        </span>
        <span className="text-orange-400 text-xs">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-3 py-2.5 space-y-2.5 bg-white">
          {recommended_because && (
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-0.5">
                Why recommended
              </p>
              <p className="text-gray-700 leading-relaxed">{recommended_because}</p>
            </div>
          )}
          {rank_reason && (
            <div>
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-0.5">
                Why this rank
              </p>
              <p className="text-gray-600 leading-relaxed">{rank_reason}</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── RestaurantCard ────────────────────────────────────────────────────────────

function RestaurantCard({ restaurant, rank }: { restaurant: Restaurant; rank: number }) {
  const [imgError, setImgError] = useState(false);

  return (
    <div className="bg-white rounded-2xl overflow-hidden border border-orange-100 shadow-sm hover:shadow-md transition-shadow duration-200">
      {/* Photo */}
      {restaurant.photo_url && !imgError ? (
        <img
          src={restaurant.photo_url}
          alt={restaurant.name}
          className="w-full h-44 object-cover"
          onError={() => setImgError(true)}
        />
      ) : (
        <div className="w-full h-44 bg-orange-50 flex items-center justify-center">
          <Utensils className="w-10 h-10 text-orange-200" />
        </div>
      )}

      {/* Content */}
      <div className="p-4 space-y-2">
        {/* Name + Rating */}
        <div className="flex items-start justify-between gap-2">
          <h3 className="font-bold text-gray-800 text-base leading-snug">
            {restaurant.name}
          </h3>
          {restaurant.rating != null && (
            <div className="flex items-center gap-1 bg-orange-50 text-orange-600 text-sm font-semibold px-2 py-0.5 rounded-full shrink-0">
              <Star className="w-3.5 h-3.5 fill-orange-400 text-orange-400" />
              {restaurant.rating.toFixed(1)}
            </div>
          )}
        </div>

        {/* Description */}
        {restaurant.description && (
          <p className="text-sm text-gray-500 leading-relaxed line-clamp-3">
            {restaurant.description}
          </p>
        )}

        {/* Address */}
        {restaurant.address && (
          <div className="flex items-start gap-1.5 text-sm text-gray-500 pt-1">
            <MapPin className="w-3.5 h-3.5 mt-0.5 shrink-0 text-orange-400" />
            <span className="leading-snug">{restaurant.address}</span>
          </div>
        )}

        {/* Opening Hours */}
        {(restaurant.opening_hours?.weekday_text?.length ?? 0) > 0 && (
          <OpeningHoursDropdown hours={restaurant.opening_hours!} />
        )}

        {/* Why chosen — collapsible */}
        <WhyChosenDropdown
          recommended_because={restaurant.recommended_because}
          rank_reason={restaurant.rank_reason}
          rank={rank}
        />

        {/* Maps Link */}
        {restaurant.maps_url && (
          <a
            href={restaurant.maps_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-sm font-semibold text-orange-500 hover:text-orange-600 transition-colors pt-1"
          >
            View on Google Maps
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        )}
      </div>
    </div>
  );
}

// ── RestaurantList ────────────────────────────────────────────────────────────

function RestaurantList({ restaurants }: { restaurants: Restaurant[] }) {
  const [showAll, setShowAll] = useState(false);

  const hasMore = restaurants.length > MAX_VISIBLE;
  const visible = showAll ? restaurants : restaurants.slice(0, MAX_VISIBLE);
  const hiddenCount = restaurants.length - MAX_VISIBLE;

  return (
    <div className="space-y-3">
      {visible.map((r, i) => (
        <RestaurantCard key={i} restaurant={r} rank={i + 1} />
      ))}

      {hasMore && (
        <button
          onClick={() => setShowAll(prev => !prev)}
          className="w-full flex items-center justify-center gap-2 py-3 px-4 rounded-2xl border border-orange-200 bg-white text-orange-500 font-semibold text-sm hover:bg-orange-50 hover:border-orange-300 transition-all duration-200 shadow-sm"
        >
          {showAll ? (
            <>
              <ChevronUp className="w-4 h-4" />
              Show less
            </>
          ) : (
            <>
              <ChevronDown className="w-4 h-4" />
              Show {hiddenCount} more restaurant{hiddenCount !== 1 ? 's' : ''}
            </>
          )}
        </button>
      )}
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatRestaurantForExport(restaurant: Restaurant): string {
  const parts = [
    `- ${restaurant.name}`,
    restaurant.description ? `  Description: ${restaurant.description}` : '',
    restaurant.address ? `  Address: ${restaurant.address}` : '',
    restaurant.rating != null ? `  Rating: ${restaurant.rating.toFixed(1)}` : '',
    restaurant.maps_url ? `  Maps: ${restaurant.maps_url}` : '',
    restaurant.recommended_because ? `  Why recommended: ${restaurant.recommended_because}` : '',
    restaurant.rank_reason ? `  Why this rank: ${restaurant.rank_reason}` : '',
  ].filter(Boolean);

  return parts.join('\n');
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function RestaurantChatbot() {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: 'assistant',
      content: "Hi! I'm FoodKakiBot, your restaurant advisor. Tell me what you're craving, your location, budget, or dietary preferences, and I'll help you decide where to eat!"
    }
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string>('');
  const [userLocation, setUserLocation] = useState<{lat: number, lng: number} | null>(null);
  const [activeTags, setActiveTags] = useState<Record<string, string>>({});
  const [tagCatalog, setTagCatalog] = useState<TagCatalog>({ cuisines: [], budgets: [], locations: [] });
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    const initSession = async () => {
      try {
        const response = await fetch('http://localhost:5000/api/session', { method: 'POST' });
        const data = await response.json();
        setSessionId(data.session_id);
      } catch {
        setSessionId(crypto.randomUUID());
      }
    };
    initSession();
  }, []);

  useEffect(() => {
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          console.log("GPS:", pos.coords.latitude, pos.coords.longitude);
          setUserLocation({ lat: pos.coords.latitude, lng: pos.coords.longitude });
        },
        (err) => console.warn('Geolocation unavailable:', err.message)
      );
    }
  }, []);

  useEffect(() => {
    fetch('http://localhost:5000/api/tags')
      .then(r => r.json())
      .then(data => setTagCatalog(data))
      .catch(() => {});
  }, []);

  const handleTagSelect = async (category: string, value: string) => {
    if (!sessionId) return;
    const res = await fetch('http://localhost:5000/api/session/tags', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, [category]: value }),
    }).catch(() => null);
    if (res?.ok) {
      const data = await res.json();
      setActiveTags(data.active_tags ?? {});
    }
  };

  const handleTagClear = async (category: string) => {
    if (!sessionId) return;
    const res = await fetch('http://localhost:5000/api/session/tags', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, [category]: null }),
    }).catch(() => null);
    if (res?.ok) {
      const data = await res.json();
      setActiveTags(data.active_tags ?? {});
    }
  };

  const buildTagMessage = (tags: Record<string, string>): string => {
    const parts: string[] = [];
    if (tags.cuisine) parts.push(tags.cuisine);
    if (tags.location) parts.push(`in ${tags.location}`);
    if (tags.budget) parts.push(`with ${tags.budget.toLowerCase()} budget`);
    return parts.length ? `Find me ${parts.join(' ')} restaurants` : 'Recommend me restaurants';
  };

  const hasEnoughTags = Object.keys(activeTags).length >= 2;

  const handleSubmit = async (overrideMessage?: string) => {
    const textMessage = overrideMessage ?? input.trim();
    if (!textMessage || loading || !sessionId) return;

    const userMessage = textMessage;
    if (!overrideMessage) setInput('');
    setMessages(prev => [...prev, { role: 'user', content: userMessage }]);
    setLoading(true);

    try {
      const response = await fetch('http://localhost:5000/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: userMessage,
          session_id: sessionId,
          lat: userLocation?.lat ?? null,
          lng: userLocation?.lng ?? null,
        })
      });

      const data = await response.json();

      if (response.ok) {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: data.response,
          restaurants: data.restaurants ?? [],
        }]);
        if (data.active_tags) setActiveTags(data.active_tags);
      } else {
        setMessages(prev => [...prev, {
          role: 'assistant',
          content: `Error: ${data.error || 'Please try again.'}`,
        }]);
      }
    } catch (error) {
      console.error('Error:', error);
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'Sorry, I could not connect to the server. Make sure the backend is running on http://localhost:5000',
      }]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleExport = () => {
    const timestamp = new Date();
    const exportLines = [
      'FoodKakiBot Chat Export',
      `Exported: ${timestamp.toLocaleString()}`,
      `Session ID: ${sessionId || 'Unavailable'}`,
      '',
      ...messages.flatMap((msg, index) => {
        const section = [
          `[${index + 1}] ${msg.role.toUpperCase()}`,
          msg.content,
        ];

        if (msg.restaurants?.length) {
          section.push('', 'Restaurants:');
          section.push(...msg.restaurants.map(formatRestaurantForExport));
        }

        section.push('');
        return section;
      }),
    ].join('\n');

    const blob = new Blob([exportLines], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    const safeSessionId = sessionId ? sessionId.slice(0, 8) : 'chat';

    link.href = url;
    link.download = `foodkakibot-chat-${safeSessionId}.txt`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex flex-col h-screen bg-gradient-to-br from-orange-50 to-amber-50">
      {/* Header */}
      <div className="bg-white shadow-sm border-b border-orange-100">
        <div className="max-w-4xl mx-auto px-4 py-4 flex items-center gap-3">
          <div className="bg-orange-500 p-2 rounded-lg">
            <Utensils className="w-6 h-6 text-white" />
          </div>
          <div className="flex-1">
            <h1 className="text-xl font-bold text-gray-800">Where Should I Eat?</h1>
            <p className="text-sm text-gray-500">AI-powered restaurant recommendations</p>
          </div>
          {sessionId && (
            <div className="hidden sm:block text-xs text-gray-400">
              Session: {sessionId.slice(0, 8)}...
            </div>
          )}
          <button
            onClick={handleExport}
            className="inline-flex items-center gap-2 rounded-full border border-orange-200 px-4 py-2 text-sm font-semibold text-orange-600 hover:bg-orange-50 transition-colors"
          >
            <Download className="w-4 h-4" />
            Export
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-6">
        <div className="max-w-4xl mx-auto space-y-4">
          {messages.map((msg, idx) => (
            <div key={idx} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              {msg.role === 'user' ? (
                <div className="max-w-2xl px-4 py-3 rounded-2xl bg-orange-500 text-white">
                  <p className="whitespace-pre-wrap">{msg.content}</p>
                </div>
              ) : msg.restaurants && msg.restaurants.length > 0 ? (
                <div className="w-full max-w-2xl space-y-3">
                  <div className="px-4 py-3 rounded-2xl bg-white text-gray-800 shadow-sm border border-orange-100">
                    <p className="whitespace-pre-wrap">{msg.content}</p>
                  </div>
                  <RestaurantList restaurants={msg.restaurants} />
                </div>
              ) : (
                <div className="max-w-2xl px-4 py-3 rounded-2xl bg-white text-gray-800 shadow-sm border border-orange-100">
                  <p className="whitespace-pre-wrap">{msg.content}</p>
                </div>
              )}
            </div>
          ))}

          {loading && (
            <div className="flex justify-start">
              <div className="bg-white px-4 py-3 rounded-2xl shadow-sm border border-orange-100">
                <Loader2 className="w-5 h-5 text-orange-500 animate-spin" />
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input */}
      <div className="bg-white border-t border-orange-100 px-4 py-4">
        <div className="max-w-4xl mx-auto">
          <TagFilterDropdown
            activeTags={activeTags}
            catalog={tagCatalog}
            onSelect={handleTagSelect}
            onClear={handleTagClear}
          />
          {hasEnoughTags && (
            <button
              onClick={() => handleSubmit(buildTagMessage(activeTags))}
              disabled={loading}
              className="w-full mb-2 py-2.5 rounded-xl bg-orange-500 text-white text-sm font-medium hover:bg-orange-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
            >
              <Send className="w-4 h-4" />
              Search with filters
            </button>
          )}
          <div className="flex gap-2">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder="Tell me what you're craving..."
              className="flex-1 px-4 py-3 rounded-full border border-orange-200 focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent"
              disabled={loading}
            />
            <button
              onClick={() => handleSubmit()}
              disabled={loading || !input.trim()}
              className="bg-orange-500 text-white p-3 rounded-full hover:bg-orange-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <Send className="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}