import json
import logging
from typing import Any, Dict, List, Optional

import boto3
import urllib3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

from config import S3Config
from models.s3_object import S3Bucket, S3Object

if not S3Config.VERIFY_TLS:
    # The operator explicitly disabled certificate verification
    # (S3FB_VERIFY_TLS=false); do not flood the log with one warning per request.
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

class S3Service:
    """Thin wrapper around boto3 for the operations the UI needs."""
    
    def __init__(self, 
                 access_key_id: str = None,
                 secret_access_key: str = None,
                 endpoint_url: str = None,
                 region: str = None,
                 multipart_threshold: int = 8,
                 chunk_size: int = 8,
                 addressing_style: str = None):
        
        self.access_key_id = access_key_id or S3Config.AWS_ACCESS_KEY_ID
        self.secret_access_key = secret_access_key or S3Config.AWS_SECRET_ACCESS_KEY
        self.endpoint_url = endpoint_url or S3Config.S3_ENDPOINT_URL
        self.region = region or S3Config.AWS_DEFAULT_REGION
        self.multipart_threshold = multipart_threshold
        self.chunk_size = chunk_size
        self.addressing_style = addressing_style or S3Config.S3_ADDRESSING_STYLE

        # Listing cache
        self._cache = {}
        self._cache_ttl = S3Config.CACHE_TTL
        
        self._client = None
        self._resource = None
        
    def _boto_config(self, **extra) -> BotoConfig:
        return BotoConfig(
            s3={'addressing_style': self.addressing_style},
            connect_timeout=10,
            read_timeout=60,
            retries={'max_attempts': 3, 'mode': 'standard'},
            **extra,
        )

    _SAME_ENDPOINT = object()

    def _make_client(self, region: str = None, endpoint_url=_SAME_ENDPOINT, **config_extra):
        return boto3.client(
            's3',
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            endpoint_url=self.endpoint_url if endpoint_url is S3Service._SAME_ENDPOINT else endpoint_url,
            region_name=region or self.region,
            verify=S3Config.verify(),
            config=self._boto_config(**config_extra),
        )

    @property
    def client(self):
        """Lazily created S3 client."""
        if self._client is None:
            try:
                self._client = self._make_client()
                logger.debug(f"S3 client initialized for endpoint {self.endpoint_url} "
                             f"({self.addressing_style} addressing)")
            except Exception as e:
                logger.error(f"Error initializing S3 client: {e}")
                raise
        return self._client

    def _client_for_region(self, region: str):
        """Client bound to another region (for buckets outside the session region)."""
        if region == self.region:
            return self.client
        logger.debug(f"Creating region-specific client for region {region}")
        try:
            return self._make_client(region=region)
        except Exception as e:
            logger.error(f"Error creating region-specific client: {e}")
            return self.client

    def _presign_client(self, bucket: str):
        """Client used to sign URLs: SigV4 and, on AWS, the bucket's own region."""
        bucket_region = self.get_bucket_region(bucket)
        if bucket_region != self.region:
            endpoint = self.endpoint_url if 's3.amazonaws.com' not in (self.endpoint_url or '') else None
            return self._make_client(region=bucket_region, endpoint_url=endpoint,
                                     signature_version='s3v4')
        return self.client
    
    def test_connection(self, bucket: str = None) -> bool:
        """Check that the credentials work.

        When `bucket` is provided the check runs against that bucket
        (list_objects_v2 with MaxKeys=1) instead of list_buckets(). This is
        needed for bucket-scoped credentials, which are often allowed to use
        one bucket but not `s3:ListAllMyBuckets`.
        """
        logger.info(f"Testing S3 connection to endpoint {self.endpoint_url}")
        try:
            if bucket:
                self.client.list_objects_v2(Bucket=bucket, MaxKeys=1)
            else:
                self.client.list_buckets()
            logger.info(f"S3 connection OK: {self.endpoint_url}")
            return True
        except (ClientError, NoCredentialsError, EndpointConnectionError) as e:
            logger.warning(f"S3 connection test failed for {self.endpoint_url}: {e}")
            return False

    def list_buckets(self) -> List[S3Bucket]:
        """List all buckets (name and creation date only)."""
        try:
            response = self.client.list_buckets()
            buckets = [
                S3Bucket(name=b['Name'], creation_date=b.get('CreationDate'))
                for b in response.get('Buckets', [])
            ]
            logger.debug(f"Listed {len(buckets)} buckets from {self.endpoint_url}")
            return buckets
        except ClientError as e:
            logger.error(f"Error listing buckets from {self.endpoint_url}: {e}")
            raise

    def get_bucket_region(self, bucket_name: str) -> str:
        """Get the region where a bucket is located"""
        try:
            response = self.client.get_bucket_location(Bucket=bucket_name)
            region = response.get('LocationConstraint')
            
            # AWS returns None for us-east-1 (default region)
            if region is None:
                region = 'us-east-1'
            
            logger.info(f"Bucket {bucket_name} is located in region: {region}")
            return region
        except ClientError as e:
            logger.warning(f"Could not determine region for bucket {bucket_name}: {e}")
            return self.region  # Fall back to current region
    
    def get_bucket_info(self, bucket_name: str) -> dict:
        """Extra bucket information (versioning, region)."""
        info = {"name": bucket_name}
        try:
            versioning = self.client.get_bucket_versioning(Bucket=bucket_name)
            info["versioning"] = versioning.get("Status", "Disabled")
        except ClientError as e:
            logger.warning(f"Could not get versioning status for bucket {bucket_name}: {e}")
            info["versioning"] = "Unknown"
        
        # Add region information
        try:
            info["region"] = self.get_bucket_region(bucket_name)
        except Exception as e:
            logger.warning(f"Could not get region for bucket {bucket_name}: {e}")
            info["region"] = "Unknown"
        
        return info

    def get_bucket_policy(self, bucket_name: str) -> Optional[Dict]:
        """Get the bucket policy"""
        try:
            response = self.client.get_bucket_policy(Bucket=bucket_name)
            policy_str = response.get('Policy')
            if policy_str:
                return json.loads(policy_str)
            return None
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'NoSuchBucketPolicy':
                logger.info(f"No policy found for bucket {bucket_name}")
                return None
            logger.error(f"Error getting policy for bucket {bucket_name}: {e}")
            raise

    def get_bucket_lifecycle(self, bucket_name: str) -> Optional[List[Dict]]:
        """Get the bucket lifecycle configuration"""
        try:
            response = self.client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
            return response.get('Rules', [])
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'NoSuchLifecycleConfiguration':
                logger.info(f"No lifecycle configuration found for bucket {bucket_name}")
                return None
            logger.error(f"Error getting lifecycle for bucket {bucket_name}: {e}")
            raise

    def put_bucket_policy(self, bucket_name: str, policy: Dict) -> bool:
        """Set the bucket policy"""
        try:
            policy_json = json.dumps(policy)
            self.client.put_bucket_policy(Bucket=bucket_name, Policy=policy_json)
            logger.info(f"Successfully updated policy for bucket {bucket_name}")
            return True
        except ClientError as e:
            logger.error(f"Error setting policy for bucket {bucket_name}: {e}")
            raise

    def delete_bucket_policy(self, bucket_name: str) -> bool:
        """Delete the bucket policy"""
        try:
            self.client.delete_bucket_policy(Bucket=bucket_name)
            logger.info(f"Successfully deleted policy for bucket {bucket_name}")
            return True
        except ClientError as e:
            logger.error(f"Error deleting policy for bucket {bucket_name}: {e}")
            raise

    def put_bucket_lifecycle(self, bucket_name: str, rules: List[Dict]) -> bool:
        """Set the bucket lifecycle configuration"""
        try:
            lifecycle_config = {'Rules': rules}
            self.client.put_bucket_lifecycle_configuration(Bucket=bucket_name, LifecycleConfiguration=lifecycle_config)
            logger.info(f"Successfully updated lifecycle for bucket {bucket_name}")
            return True
        except ClientError as e:
            logger.error(f"Error setting lifecycle for bucket {bucket_name}: {e}")
            raise

    def delete_bucket_lifecycle(self, bucket_name: str) -> bool:
        """Delete the bucket lifecycle configuration"""
        try:
            self.client.delete_bucket_lifecycle(Bucket=bucket_name)
            logger.info(f"Successfully deleted lifecycle for bucket {bucket_name}")
            return True
        except ClientError as e:
            logger.error(f"Error deleting lifecycle for bucket {bucket_name}: {e}")
            raise
    
    def list_objects(self, 
                    bucket: str, 
                    prefix: str = '', 
                    delimiter: str = '/',
                    max_keys: int = 1000,
                    use_cache: bool = True,
                    continuation_token: str = None) -> dict:
        """
        List objects in a bucket using native S3 pagination.

        Returns:
            dict: {objects, common_prefixes, next_token, is_truncated, total}
        """
        logger.debug(f"Listing s3://{bucket}/{prefix} (delimiter={delimiter!r}, max_keys={max_keys})")

        bucket_region = self.get_bucket_region(bucket)
        client_to_use = self._client_for_region(bucket_region)

        try:
            params = {
                'Bucket': bucket,
                'Prefix': prefix,
                'Delimiter': delimiter,
                'MaxKeys': max_keys
            }
            if continuation_token:
                params['ContinuationToken'] = continuation_token
            response = client_to_use.list_objects_v2(**params)
            objects = []
            for obj in response.get('Contents', []):
                s3_obj = S3Object.from_s3_response(obj, bucket)
                objects.append(s3_obj)
            common_prefixes = [p['Prefix'] for p in response.get('CommonPrefixes', [])]
            next_token = response.get('NextContinuationToken')
            is_truncated = response.get('IsTruncated', False)
            total = response.get('KeyCount', len(objects))
            logger.debug(f"Listed {len(objects)} objects, {len(common_prefixes)} prefixes, truncated={is_truncated}")
            return {
                'objects': objects,
                'common_prefixes': common_prefixes,
                'next_token': next_token,
                'is_truncated': is_truncated,
                'total': total
            }
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            
            if error_code == 'PermanentRedirect':
                logger.error(f"PermanentRedirect error for bucket '{bucket}': {error_message}")
                logger.error(f"Bucket '{bucket}' is in a different region. Current region: {self.region}, Bucket region: {bucket_region}")
                raise Exception(f"Bucket '{bucket}' is in region '{bucket_region}', but you're connecting to region '{self.region}'. Please use the correct regional endpoint.")
            else:
                logger.error(f"Error listing objects in bucket '{bucket}' with prefix '{prefix}': {e}")
                raise
    
    def list_objects_recursive(self, bucket: str, prefix: str = '') -> List[Dict]:
        """
        List ALL objects under a prefix recursively (handling pagination)
        Returns a list of dictionaries with object info
        """
        logger.info(f"Recursively listing all objects in {bucket}/{prefix}")
        
        # Ensure prefix ends with / if it's not empty, to avoid partial matches
        # e.g. prefix 'foo' matching 'foobar'
        if prefix and not prefix.endswith('/'):
            prefix += '/'
            
        try:
            paginator = self.client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
            
            all_objects = []
            
            for page in pages:
                if 'Contents' not in page:
                    continue
                    
                for obj in page['Contents']:
                    # Convert to simple dict structure for frontend
                    all_objects.append({
                        'key': obj['Key'],
                        'size': obj['Size'],
                        'last_modified': obj['LastModified'].isoformat() if hasattr(obj['LastModified'], 'isoformat') else str(obj['LastModified']),
                        'etag': obj['ETag'].strip('"')
                    })
            
            logger.info(f"Found {len(all_objects)} objects recursively in {bucket}/{prefix}")
            return all_objects
            
        except ClientError as e:
            logger.error(f"Error recursively listing objects in {bucket}/{prefix}: {e}")
            raise

    def get_object_info(self, bucket: str, key: str) -> Optional[S3Object]:
        """Get detailed information about an object"""
        try:
            # Get basic information
            head_response = self.client.head_object(Bucket=bucket, Key=key)
            
            s3_obj = S3Object(
                key=key,
                bucket=bucket,
                size=head_response.get('ContentLength', 0),
                last_modified=head_response.get('LastModified'),
                etag=head_response.get('ETag', '').strip('"'),
                content_type=head_response.get('ContentType', ''),
                metadata=head_response.get('Metadata', {}),
                storage_class=head_response.get('StorageClass', 'STANDARD')
            )
            
            # Get tags if enabled
            if S3Config.INCLUDE_TAGS:
                try:
                    tag_response = self.client.get_object_tagging(Bucket=bucket, Key=key)
                    s3_obj.tags = {tag['Key']: tag['Value'] for tag in tag_response.get('TagSet', [])}
                except ClientError:
                    pass
            
            # Get versions if enabled
            if S3Config.INCLUDE_VERSIONS:
                try:
                    versions_response = self.client.list_object_versions(
                        Bucket=bucket,
                        Prefix=key,
                        MaxKeys=1000
                    )
                    
                    versions = []
                    for version in versions_response.get('Versions', []):
                        if version['Key'] == key:
                            versions.append({
                                'version_id': version['VersionId'],
                                'is_latest': version['IsLatest'],
                                'last_modified': version['LastModified'],
                                'size': version['Size'],
                                'etag': version['ETag'].strip('"')
                            })
                    
                    s3_obj.versions = versions
                    
                except ClientError:
                    pass
            
            return s3_obj
            
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return None
            logger.error(f"Error getting object info for {bucket}/{key}: {e}")
            raise
    
    def get_object_versions(self, bucket: str, key: str) -> List[Dict]:
        """Get all versions of an object"""
        logger.info(f"Getting versions for object {bucket}/{key}")
        try:
            response = self.client.list_object_versions(
                Bucket=bucket,
                Prefix=key,
                MaxKeys=1000
            )
            
            versions = []
            for version in response.get('Versions', []):
                if version['Key'] == key:
                    versions.append({
                        'version_id': version['VersionId'],
                        'is_latest': version['IsLatest'],
                        'last_modified': version['LastModified'],
                        'size': version['Size'],
                        'etag': version['ETag'].strip('"'),
                        'storage_class': version.get('StorageClass', 'STANDARD')
                    })
            
            # Sort versions by last modified (newest first)
            versions.sort(key=lambda x: x['last_modified'], reverse=True)
            
            logger.info(f"Found {len(versions)} versions for object {bucket}/{key}")
            return versions
            
        except ClientError as e:
            logger.error(f"Error getting versions for {bucket}/{key}: {e}")
            return []
    
    def get_objects_metadata(self, bucket: str, keys: List[str]) -> Dict[str, Dict]:
        """Get metadata for multiple objects"""
        logger.info(f"Getting metadata for {len(keys)} objects in bucket {bucket}")
        metadata_dict = {}
        
        for key in keys:
            try:
                head_response = self.client.head_object(Bucket=bucket, Key=key)
                metadata = head_response.get('Metadata', {})
                if metadata:
                    metadata_dict[key] = metadata
            except ClientError as e:
                logger.warning(f"Could not get metadata for {bucket}/{key}: {e}")
                continue
        
        logger.info(f"Retrieved metadata for {len(metadata_dict)} objects")
        return metadata_dict
    
    def generate_presigned_url_for_version(self, bucket: str, key: str, version_id: str,
                                           operation: str = 'get_object',
                                           expires_in: int = 3600) -> Optional[str]:
        """Presigned URL for a specific version of an object."""
        try:
            url = self._presign_client(bucket).generate_presigned_url(
                operation,
                Params={'Bucket': bucket, 'Key': key, 'VersionId': version_id},
                ExpiresIn=expires_in,
            )
            logger.debug(f"Generated presigned URL for version {version_id} of {bucket}/{key}")
            return url
        except ClientError as e:
            logger.error(f"Error generating presigned URL for version {version_id} of {bucket}/{key}: {e}")
            return None

    def _transfer_config(self) -> TransferConfig:
        mb = 1024 * 1024
        # S3 requires parts of at least 5 MB (except the last one).
        threshold = max(int(self.multipart_threshold or 8), 5) * mb
        chunk = max(int(self.chunk_size or 8), 5) * mb
        return TransferConfig(multipart_threshold=threshold, multipart_chunksize=chunk)

    def upload_object_stream(self, bucket: str, key: str, file_stream, metadata: Dict = None,
                             content_type: str = None) -> bool:
        """Upload an object straight from a stream (no temporary file)."""
        try:
            extra_args = {}
            if metadata:
                extra_args['Metadata'] = metadata
            if content_type:
                extra_args['ContentType'] = content_type
            self.client.upload_fileobj(file_stream, bucket, key, ExtraArgs=extra_args,
                                       Config=self._transfer_config())
            logger.info(f"Uploaded {bucket}/{key}")
            self._clear_cache_for_bucket(bucket)
            return True
        except ClientError as e:
            logger.error(f"Error uploading stream to {bucket}/{key}: {e}")
            return False

    def delete_object(self, bucket: str, key: str) -> bool:
        """Delete an object (creates a delete marker on versioned buckets)."""
        try:
            self.client.delete_object(Bucket=bucket, Key=key)
            logger.info(f"Deleted {bucket}/{key}")
            self._clear_cache_for_bucket(bucket)
            return True
        except ClientError as e:
            logger.error(f"Error deleting {bucket}/{key}: {e}")
            return False

    def permanently_delete_all_versions(self, bucket: str, key: str) -> Dict[str, Any]:
        """
        Permanently delete an object and ALL its versions + delete markers.
        This is irreversible.

        Returns:
            dict: {deleted_count, error_count, errors}
        """
        logger.info(f"Permanently deleting all versions of {bucket}/{key}")
        deleted_count = 0
        error_count = 0
        errors = []

        try:
            # Collect all versions and delete markers for this exact key
            paginator = self.client.get_paginator('list_object_versions')
            objects_to_delete = []

            for page in paginator.paginate(Bucket=bucket, Prefix=key):
                for version in page.get('Versions', []):
                    if version['Key'] == key:
                        objects_to_delete.append({'Key': version['Key'], 'VersionId': version['VersionId']})
                for marker in page.get('DeleteMarkers', []):
                    if marker['Key'] == key:
                        objects_to_delete.append({'Key': marker['Key'], 'VersionId': marker['VersionId']})

            if not objects_to_delete:
                logger.warning(f"No versions or delete markers found for {bucket}/{key}")
                return {'deleted_count': 0, 'error_count': 0, 'errors': []}

            # Delete in batches of 1000 (S3 limit)
            for i in range(0, len(objects_to_delete), 1000):
                batch = objects_to_delete[i:i + 1000]
                response = self.client.delete_objects(
                    Bucket=bucket,
                    Delete={'Objects': batch, 'Quiet': False}
                )
                deleted_count += len(response.get('Deleted', []))
                for err in response.get('Errors', []):
                    error_count += 1
                    errors.append(f"{err['Key']} v{err['VersionId']}: {err['Message']}")

            self._clear_cache_for_bucket(bucket)
            logger.info(f"Permanently deleted {deleted_count} versions of {bucket}/{key}")
            return {'deleted_count': deleted_count, 'error_count': error_count, 'errors': errors}

        except ClientError as e:
            logger.error(f"Error permanently deleting {bucket}/{key}: {e}")
            raise

    def delete_prefix_recursive(self, bucket: str, prefix: str) -> Dict[str, Any]:
        """
        Delete all objects under a given prefix recursively
        
        Args:
            bucket: S3 bucket name
            prefix: Prefix/folder to delete
            
        Returns:
            dict: {
                'deleted_count': number of objects deleted,
                'error_count': number of errors encountered,
                'errors': list of error messages
            }
        """
        logger.info(f"Starting recursive delete for {bucket}/{prefix}")
        
        # Ensure prefix ends with / if it's not empty
        if prefix and not prefix.endswith('/'):
            prefix += '/'
        
        deleted_count = 0
        error_count = 0
        errors = []
        
        try:
            # List all objects under the prefix (no delimiter to get all recursively)
            paginator = self.client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
            
            # Collect objects to delete in batches
            objects_to_delete = []
            
            for page in pages:
                if 'Contents' not in page:
                    continue
                
                for obj in page['Contents']:
                    objects_to_delete.append({'Key': obj['Key']})
                    
                    # Delete in batches of 1000 (S3 limit)
                    if len(objects_to_delete) >= 1000:
                        result = self._delete_batch(bucket, objects_to_delete)
                        deleted_count += result['deleted']
                        error_count += result['errors']
                        if result['error_messages']:
                            errors.extend(result['error_messages'])
                        objects_to_delete = []
            
            # Delete remaining objects
            if objects_to_delete:
                result = self._delete_batch(bucket, objects_to_delete)
                deleted_count += result['deleted']
                error_count += result['errors']
                if result['error_messages']:
                    errors.extend(result['error_messages'])
            
            # Clear cache for this bucket
            self._clear_cache_for_bucket(bucket)
            
            logger.info(f"Recursive delete completed: {deleted_count} objects deleted, {error_count} errors")
            
            return {
                'deleted_count': deleted_count,
                'error_count': error_count,
                'errors': errors
            }
            
        except ClientError as e:
            logger.error(f"Error during recursive delete of {bucket}/{prefix}: {e}")
            return {
                'deleted_count': deleted_count,
                'error_count': error_count + 1,
                'errors': errors + [str(e)]
            }
    
    def _delete_batch(self, bucket: str, objects: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Delete a batch of objects using S3's batch delete API
        
        Args:
            bucket: S3 bucket name
            objects: List of objects in format [{'Key': 'object_key'}, ...]
            
        Returns:
            dict: {'deleted': count, 'errors': count, 'error_messages': []}
        """
        try:
            response = self.client.delete_objects(
                Bucket=bucket,
                Delete={'Objects': objects}
            )
            
            deleted = len(response.get('Deleted', []))
            errors_list = response.get('Errors', [])
            error_count = len(errors_list)
            error_messages = [f"{err['Key']}: {err['Message']}" for err in errors_list]
            
            if error_messages:
                logger.warning(f"Batch delete had {error_count} errors: {error_messages}")
            
            return {
                'deleted': deleted,
                'errors': error_count,
                'error_messages': error_messages
            }
            
        except ClientError as e:
            logger.error(f"Error in batch delete: {e}")
            return {
                'deleted': 0,
                'errors': len(objects),
                'error_messages': [f"Batch delete failed: {str(e)}"]
            }
    
    def generate_presigned_url(self, bucket: str, key: str, operation: str = 'get_object',
                               expires_in: int = 3600) -> Optional[str]:
        """Presigned URL for an object."""
        try:
            return self._presign_client(bucket).generate_presigned_url(
                operation,
                Params={'Bucket': bucket, 'Key': key},
                ExpiresIn=expires_in,
            )
        except ClientError as e:
            logger.error(f"Error generating presigned URL for {bucket}/{key}: {e}")
            return None

    def _clear_cache_for_bucket(self, bucket: str):
        """Drop cached listings for one bucket."""
        keys_to_remove = [k for k in self._cache.keys() if k.startswith(f"{bucket}:")]
        for key in keys_to_remove:
            del self._cache[key]
    
    def clear_cache(self):
        """Drop all cached listings."""
        self._cache.clear()
        logger.info("Cache cleared")
    
    def list_delete_markers(self, bucket: str, prefix: str = '', max_keys: int = 1000, key_marker: str = None, version_id_marker: str = None) -> List[Dict]:
        """List delete markers in a bucket (optionally filtered by prefix)"""
        logger.info(f"Listing delete markers in bucket '{bucket}' with prefix '{prefix}'")
        try:
            params = {
                'Bucket': bucket,
                'Prefix': prefix,
                'MaxKeys': max_keys
            }
            if key_marker:
                params['KeyMarker'] = key_marker
            if version_id_marker:
                params['VersionIdMarker'] = version_id_marker
            response = self.client.list_object_versions(**params)
            delete_markers = []
            for marker in response.get('DeleteMarkers', []):
                delete_markers.append({
                    'key': marker['Key'],
                    'version_id': marker['VersionId'],
                    'is_latest': marker.get('IsLatest', False),
                    'last_modified': marker['LastModified'],
                    'owner': marker.get('Owner', {}).get('DisplayName', None)
                })
            logger.info(f"Found {len(delete_markers)} delete markers in bucket '{bucket}' with prefix '{prefix}'")
            return delete_markers
        except ClientError as e:
            logger.error(f"Error listing delete markers in bucket '{bucket}' with prefix '{prefix}': {e}")
            return []
    
    def get_object_stream(self, bucket: str, key: str, version_id: str = None):
        """
        Get an object stream and its content type.
        Returns (body_stream, content_type, content_length)
        """
        try:
            params = {'Bucket': bucket, 'Key': key}
            if version_id:
                params['VersionId'] = version_id
            response = self.client.get_object(**params)
            return (
                response['Body'],
                response.get('ContentType', 'application/octet-stream'),
                response.get('ContentLength', 0)
            )
        except ClientError as e:
            logger.error(f"Error getting object stream for {bucket}/{key}: {e}")
            raise

    def get_object_partial(self, bucket: str, key: str, range_header: str = None):
        """GET an object, optionally with an HTTP Range header. Returns the raw boto3 response."""
        params = {'Bucket': bucket, 'Key': key}
        if range_header:
            params['Range'] = range_header
        return self.client.get_object(**params)

    # ---- ZIP downloads -------------------------------------------------

    @staticmethod
    def _safe_arcname(name: str) -> str:
        """Make an object key safe to use as a path inside a ZIP file
        (no absolute paths, no '..' components)."""
        parts = [p for p in name.replace('\\', '/').split('/') if p not in ('', '.', '..')]
        return '/'.join(parts) or 'unnamed'

    def _zip_stream(self, bucket: str, entries: List[Dict[str, Any]]):
        """Yield a ZIP archive chunk by chunk without holding it in memory.

        `entries` is a list of {'key': ..., 'arcname': ..., 'mtime': datetime|None}. zipfile writes to a
        non-seekable sink, so every member uses a data descriptor and the
        archive can be produced strictly sequentially.
        """
        import zipfile

        class _Sink:
            def __init__(self):
                self.chunks = []

            def write(self, data):
                self.chunks.append(bytes(data))
                return len(data)

            def flush(self):
                pass

            def drain(self):
                out, self.chunks = b''.join(self.chunks), []
                return out

        sink = _Sink()
        chunk_size = 8 * 1024 * 1024
        with zipfile.ZipFile(sink, 'w', zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            for entry in entries:
                arcname = self._safe_arcname(entry['arcname'])
                try:
                    obj = self.client.get_object(Bucket=bucket, Key=entry['key'])
                    body = obj['Body']
                    mtime = entry.get('mtime') or obj.get('LastModified')
                    zinfo = zipfile.ZipInfo(arcname, date_time=mtime.timetuple()[:6] if mtime else (1980, 1, 1, 0, 0, 0))
                    zinfo.compress_type = zipfile.ZIP_DEFLATED
                    with zf.open(zinfo, 'w', force_zip64=True) as member:
                        while True:
                            chunk = body.read(chunk_size)
                            if not chunk:
                                break
                            member.write(chunk)
                            data = sink.drain()
                            if data:
                                yield data
                except Exception as e:
                    logger.error(f"Error adding {entry['key']} to zip: {e}")
                    zf.writestr(f"{arcname}.error.txt", f"Error downloading this file: {e}")
                data = sink.drain()
                if data:
                    yield data
        data = sink.drain()
        if data:
            yield data

    def download_folder_as_zip(self, bucket: str, prefix: str, max_total_size_gb: int = 10):
        """Stream a ZIP with every object under `prefix` (paths relative to it)."""
        if prefix and not prefix.endswith('/'):
            prefix += '/'

        paginator = self.client.get_paginator('list_objects_v2')
        entries, total_size = [], 0
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get('Contents', []):
                if obj['Key'].endswith('/'):  # folder marker
                    continue
                entries.append({'key': obj['Key'], 'arcname': obj['Key'][len(prefix):],
                                'mtime': obj.get('LastModified')})
                total_size += obj.get('Size', 0)

        max_size_bytes = max_total_size_gb * 1024 ** 3
        if total_size > max_size_bytes:
            raise ValueError(f"Folder size ({total_size / 1024 ** 3:.2f} GB) exceeds the maximum "
                             f"allowed size ({max_total_size_gb} GB)")
        logger.info(f"Streaming ZIP of {len(entries)} files ({total_size / 1024 ** 2:.1f} MB) "
                    f"from {bucket}/{prefix}")
        return self._zip_stream(bucket, entries)

    def download_keys_as_zip(self, bucket: str, keys: List[str], max_total_size_gb: int = 10):
        """Stream a ZIP with a selection of keys; folders (ending in '/') are expanded."""
        entries, total_size = [], 0
        paginator = self.client.get_paginator('list_objects_v2')
        for key in keys:
            if key.endswith('/'):
                for page in paginator.paginate(Bucket=bucket, Prefix=key):
                    for obj in page.get('Contents', []):
                        if not obj['Key'].endswith('/'):
                            entries.append({'key': obj['Key'], 'arcname': obj['Key'],
                                            'mtime': obj.get('LastModified')})
                            total_size += obj.get('Size', 0)
            else:
                try:
                    head = self.client.head_object(Bucket=bucket, Key=key)
                    entries.append({'key': key, 'arcname': key})
                    total_size += head.get('ContentLength', 0)
                except Exception as e:
                    logger.warning(f"Could not stat {bucket}/{key}: {e}")

        if not entries:
            raise ValueError("No files found in selection")
        if total_size > max_total_size_gb * 1024 ** 3:
            raise ValueError(f"Selection size ({total_size / 1024 ** 3:.2f} GB) exceeds the limit "
                             f"({max_total_size_gb} GB)")
        logger.info(f"Streaming ZIP of {len(entries)} files ({total_size / 1024 ** 2:.1f} MB) from {bucket}")
        return self._zip_stream(bucket, entries)

    def get_object_head(self, bucket: str, key: str) -> Dict[str, Any]:
        """HEAD an object (raises ClientError if it does not exist)."""
        return self.client.head_object(Bucket=bucket, Key=key)

    def get_object_content_type(self, bucket: str, key: str) -> str:
        """Get content type of an object"""
        try:
            response = self.client.head_object(Bucket=bucket, Key=key)
            return response.get('ContentType', 'application/octet-stream')
        except ClientError as e:
            logger.error(f"Error getting content type for {bucket}/{key}: {e}")
            return 'application/octet-stream'
    
    def search_objects(self, bucket: str, search_term: str = '', extensions: str = '', prefix: str = '', max_keys: int = 1000) -> List[S3Object]:
        """
        Search for objects in a bucket by name pattern and file extensions
        
        Args:
            bucket: Bucket name
            search_term: Search term to match in object keys
            extensions: Comma-separated list of file extensions to filter by
            prefix: Optional prefix to limit search scope
            max_keys: Maximum number of objects to return
            
        Returns:
            List of S3Object matching the search criteria
        """
        logger.info(f"Searching objects in bucket '{bucket}' with term '{search_term}', extensions '{extensions}', prefix '{prefix}'")
        
        bucket_region = self.get_bucket_region(bucket)
        client_to_use = self._client_for_region(bucket_region)

        try:
            # S3 can only filter by prefix server-side: list everything under
            # the current prefix and filter by name/extension here.
            search_prefix = prefix

            # Get all objects under the prefix
            all_objects = []
            continuation_token = None
            
            while True:
                params = {
                    'Bucket': bucket,
                    'Prefix': search_prefix,
                    'MaxKeys': min(max_keys, 1000)  # S3 max is 1000
                }
                
                if continuation_token:
                    params['ContinuationToken'] = continuation_token
                
                response = client_to_use.list_objects_v2(**params)
                
                for obj in response.get('Contents', []):
                    s3_obj = S3Object.from_s3_response(obj, bucket)
                    all_objects.append(s3_obj)
                
                # Check if we need to continue pagination
                if not response.get('IsTruncated', False) or len(all_objects) >= max_keys:
                    break
                    
                continuation_token = response.get('NextContinuationToken')
                if not continuation_token:
                    break
            
            # Apply filters
            filtered_objects = []
            extension_list = [ext.strip() for ext in extensions.split(',')] if extensions else []
            
            for obj in all_objects:
                # Skip if we've reached max_keys
                if len(filtered_objects) >= max_keys:
                    break
                
                # Apply search term filter
                if search_term and search_term.lower() not in obj.key.lower():
                    continue
                
                # Apply extension filter
                if extension_list:
                    obj_ext = self._get_file_extension(obj.key)
                    if not any(obj_ext.lower() == ext.lower() for ext in extension_list):
                        continue
                
                filtered_objects.append(obj)
            
            logger.info(f"Search completed: {len(filtered_objects)} objects found out of {len(all_objects)} total")
            return filtered_objects
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            
            if error_code == 'PermanentRedirect':
                logger.error(f"PermanentRedirect error for bucket '{bucket}': {error_message}")
                logger.error(f"Bucket '{bucket}' is in a different region. Current region: {self.region}, Bucket region: {bucket_region}")
                raise Exception(f"Bucket '{bucket}' is in region '{bucket_region}', but you're connecting to region '{self.region}'. Please use the correct regional endpoint.")
            else:
                logger.error(f"Error searching objects in bucket '{bucket}': {e}")
                raise
    
    def _get_file_extension(self, key: str) -> str:
        """Extract file extension from object key"""
        if '.' not in key:
            return ''
        
        # Handle cases where the key might be a path
        filename = key.split('/')[-1]
        if '.' not in filename:
            return ''
        
        return '.' + filename.split('.')[-1].lower()
    
    def get_object_range(self, bucket: str, key: str, byte_range: tuple = (0, 2 * 1024 * 1024)):
        """Download only part of an object. `byte_range` is (start, end), end exclusive."""
        range_header = f"bytes={byte_range[0]}-{byte_range[1] - 1}"
        response = self.client.get_object(Bucket=bucket, Key=key, Range=range_header)
        return response['Body']
