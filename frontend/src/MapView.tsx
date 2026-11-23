import { useEffect, useRef } from 'react'
import { AttributionControl, Map as MapLibreMap, Marker, NavigationControl, Popup } from 'maplibre-gl'
import type { Complaint } from './types'

interface MapViewProps {
  complaints: Complaint[]
  onPick?: (latitude: number, longitude: number) => void
  className?: string
}

const defaultStyle = {
  version: 8 as const,
  sources: {
    osm: {
      type: 'raster' as const,
      tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
      tileSize: 256,
      attribution: '&copy; OpenStreetMap contributors',
    },
  },
  layers: [{ id: 'osm', type: 'raster' as const, source: 'osm' }],
}

export default function MapView({ complaints, onPick, className = '' }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const markersRef = useRef<Marker[]>([])
  const onPickRef = useRef(onPick)

  useEffect(() => {
    onPickRef.current = onPick
  }, [onPick])

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    const map = new MapLibreMap({
      container: containerRef.current,
      style: import.meta.env.VITE_MAP_STYLE_URL || defaultStyle,
      center: [78.96, 20.59],
      zoom: 4.3,
      attributionControl: false,
    })
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    map.addControl(new AttributionControl({ compact: true }), 'bottom-right')
    map.on('click', (event) => onPickRef.current?.(event.lngLat.lat, event.lngLat.lng))
    mapRef.current = map
    return () => {
      markersRef.current.forEach((marker) => marker.remove())
      markersRef.current = []
      map.remove()
      mapRef.current = null
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const updateMarkers = () => {
      markersRef.current.forEach((marker) => marker.remove())
      markersRef.current = complaints.flatMap((complaint) => {
        if (complaint.latitude == null || complaint.longitude == null) return []
        const element = document.createElement('button')
        element.type = 'button'
        element.className = `map-marker severity-${complaint.severity.toLowerCase()}`
        element.setAttribute('aria-label', `${complaint.id}: ${complaint.severity} severity`)
        element.title = `${complaint.id} · ${complaint.road_name || 'Road damage'}`
        const popup = new Popup({ offset: 18, closeButton: false }).setHTML(
          `<strong>${complaint.id}</strong><br>${escapeHtml(complaint.road_name || 'Road damage')}<br>${escapeHtml(complaint.status.replaceAll('_', ' '))}`,
        )
        return [new Marker({ element }).setLngLat([complaint.longitude, complaint.latitude]).setPopup(popup).addTo(map)]
      })
    }
    if (map.isStyleLoaded()) updateMarkers()
    else map.once('load', updateMarkers)
  }, [complaints])

  return (
    <div className={`map-canvas ${className}`}>
      <div ref={containerRef} className="map-host" />
      {onPick && <span className="map-hint">Click on the map to set the report location</span>}
    </div>
  )
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!)
}
