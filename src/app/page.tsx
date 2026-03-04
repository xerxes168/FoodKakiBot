'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Send, Utensils, Loader2, MapPin, ExternalLink, Star, Clock } from 'lucide-react';

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
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
  restaurants?: Restaurant[];
}

// ── OpeningHoursDropdown ──────────────────────────────────────────────────────

function OpeningHoursDropdown({ hours }: { hours: OpeningHours }) {
  const [open, setOpen] = useState(false);

  const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  const todayIndex = new Date().getDay();
  const todayName = days[todayIndex];

  // Find today's hours from weekday_text
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

// ── RestaurantCard ────────────────────────────────────────────────────────────

function RestaurantCard({ restaurant }: { restaurant: Restaurant }) {
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

// ── Helpers ───────────────────────────────────────────────────────────────────

function extractIntro(content: string): string {
  const firstStar = content.indexOf('**');
  const firstAsterisk = content.indexOf('* ');
  const cutoff = Math.min(
    firstStar     === -1 ? Infinity : firstStar,
    firstAsterisk === -1 ? Infinity : firstAsterisk
  );
  if (cutoff === Infinity) return '';
  return content.substring(0, cutoff).trim();
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
        (pos) => setUserLocation({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
        (err) => console.warn('Geolocation unavailable:', err.message)
      );
    }
  }, []);

  const handleSubmit = async () => {
    if (!input.trim() || loading || !sessionId) return;

    const userMessage = input.trim();
    setInput('');
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
            <div className="text-xs text-gray-400">
              Session: {sessionId.slice(0, 8)}...
            </div>
          )}
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
                  {extractIntro(msg.content) && (
                    <div className="px-4 py-3 rounded-2xl bg-white text-gray-800 shadow-sm border border-orange-100">
                      <p>{extractIntro(msg.content)}</p>
                    </div>
                  )}
                  {msg.restaurants.map((r, i) => (
                    <RestaurantCard key={i} restaurant={r} />
                  ))}
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
              onClick={handleSubmit}
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