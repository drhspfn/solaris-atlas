import { ArrowRight } from 'lucide-react';
import { Link } from '@/lib/router';
import { Breadcrumbs } from '@/components/ui/Breadcrumbs';
import { PageHeader } from '@/components/ui/PageHeader';

export default function NotFound() {
  return (
    <div className="page-container not-found">
      <Breadcrumbs items={[{ label: 'Archive', to: '/' }, { label: '404' }]} />
      <PageHeader
        title="This path has faded"
        description={
          <Link className="text-link" to="/">
            Return to the archive <ArrowRight size={16} />
          </Link>
        }
      />
    </div>
  );
}
