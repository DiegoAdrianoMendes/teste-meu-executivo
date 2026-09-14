import { useState } from 'react';
import type { WhatsAppMessage } from '@/types';
import { AlertCircle, Check, CheckCheck, Loader2, Mic, Image as ImageIcon, FileText } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { toast } from '@/components/ui/use-toast';
import { apiDownload } from '@/services/http';

interface ExtendedMessage extends WhatsAppMessage {
  messageType?: 'text' | 'audio' | 'image' | 'video' | 'document';
}

interface MessageBubbleProps {
  message: ExtendedMessage;
}

function formatTime(ts: string) {
  return new Date(ts).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isOwn = !message.isFromClient;
  const msgType = message.messageType ?? 'text';
  const [converting, setConverting] = useState(false);

  const handleConvertToPdf = async () => {
    setConverting(true);
    try {
      const blob = await apiDownload(`/api/v1/whatsapp/messages/${message.id}/to-pdf`, { method: 'POST' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `mensagem-${message.id}.pdf`;
      link.click();
      URL.revokeObjectURL(url);
      toast({ title: 'PDF criado a partir da imagem.' });
    } catch (error) {
      toast({
        variant: 'destructive',
        title: 'Não foi possível criar o PDF',
        description: error instanceof Error ? error.message : 'A imagem não está disponível.',
      });
    } finally {
      setConverting(false);
    }
  };

  return (
    <div className={`flex ${isOwn ? 'justify-end' : 'justify-start'} mb-1`}>
      <div
        className={`relative max-w-[75%] rounded-2xl px-3 py-2 text-sm leading-relaxed ${
          isOwn
            ? 'bg-primary text-primary-foreground rounded-br-md'
            : 'bg-card border rounded-bl-md text-foreground'
        }`}
      >
        {/* Audio message */}
        {msgType === 'audio' && (
          <div className="flex items-center gap-2 mb-1">
            <div className={`w-7 h-7 rounded-full flex items-center justify-center ${isOwn ? 'bg-primary-foreground/20' : 'bg-primary/10'}`}>
              <Mic className={`h-3.5 w-3.5 ${isOwn ? 'text-primary-foreground' : 'text-primary'}`} />
            </div>
            <div className="flex-1">
              <div className={`h-1 rounded-full ${isOwn ? 'bg-primary-foreground/30' : 'bg-border'} w-20`} />
            </div>
          </div>
        )}

        {/* Image message */}
        {msgType === 'image' && (
          <div className="flex items-center gap-2 mb-1">
            <div className={`w-7 h-7 rounded-full flex items-center justify-center ${isOwn ? 'bg-primary-foreground/20' : 'bg-primary/10'}`}>
              <ImageIcon className={`h-3.5 w-3.5 ${isOwn ? 'text-primary-foreground' : 'text-primary'}`} />
            </div>
            <span className="text-xs opacity-70">Imagem</span>
          </div>
        )}

        {/* Text content */}
        {(msgType === 'text' || !msgType) && (
          <p className="whitespace-pre-wrap break-words">{message.text}</p>
        )}

        {/* Audio transcript */}
        {msgType === 'audio' && message.transcript && (
          <div className={`mt-1.5 pt-1.5 border-t ${isOwn ? 'border-primary-foreground/20' : 'border-border'}`}>
            <div className="flex items-start gap-1">
              <Mic className={`h-3 w-3 mt-0.5 shrink-0 ${isOwn ? 'text-primary-foreground/60' : 'text-muted-foreground'}`} />
              <p className={`min-w-0 text-xs leading-relaxed ${isOwn ? 'text-primary-foreground/80' : 'text-muted-foreground'}`}>
                {message.transcript}
              </p>
            </div>
          </div>
        )}

        {/* Image OCR transcript */}
        {msgType === 'image' && message.imageTranscript && (
          <div className={`mt-1.5 pt-1.5 border-t ${isOwn ? 'border-primary-foreground/20' : 'border-border'}`}>
            <div className="flex items-start gap-1 mb-1">
              <FileText className={`h-3 w-3 mt-0.5 shrink-0 ${isOwn ? 'text-primary-foreground/60' : 'text-muted-foreground'}`} />
              <p className={`min-w-0 text-xs leading-relaxed ${isOwn ? 'text-primary-foreground/80' : 'text-muted-foreground'}`}>
                {message.imageTranscript}
              </p>
            </div>
            <Button
              variant="ghost"
              size="sm"
              className={`h-5 text-xs px-2 ${isOwn ? 'text-primary-foreground/70 hover:bg-primary-foreground/10' : 'text-muted-foreground hover:bg-accent'}`}
              disabled={converting}
              onClick={() => void handleConvertToPdf()}
            >
              {converting ? <><Loader2 className="mr-1 h-3 w-3 animate-spin" />Convertendo</> : 'Converter para PDF'}
            </Button>
          </div>
        )}

        <div className={`flex items-center justify-end gap-1 mt-0.5 ${isOwn ? 'text-primary-foreground/60' : 'text-muted-foreground'}`}>
          <span className="text-2xs tabular-nums">{formatTime(message.timestamp)}</span>
          {isOwn && (
            message.status === 'delivery_uncertain' ? (
              <AlertCircle
                aria-label="Entrega incerta; reconciliação necessária"
                className="h-3 w-3 text-amber-300"
              />
            ) : message.status === 'failed' ? (
              <AlertCircle className="h-3 w-3 text-red-300" />
            ) : message.status === 'pending' ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : message.status === 'read' ? (
              <CheckCheck className="h-3 w-3 text-blue-300" />
            ) : message.status === 'delivered' ? (
              <CheckCheck className="h-3 w-3" />
            ) : (
              <Check className="h-3 w-3" />
            )
          )}
        </div>
      </div>
    </div>
  );
}
